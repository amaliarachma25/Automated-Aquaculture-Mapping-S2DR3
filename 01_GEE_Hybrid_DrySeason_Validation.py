import ee
import geemap
import os

# --- 1. INISIALISASI ---
try:
    ee.Initialize()
    print("Berhasil terhubung ke Google Earth Engine.")
except Exception as e:
    ee.Authenticate()
    ee.Initialize()
    print("Berhasil terhubung setelah autentikasi.")

# --- 2. CONFIG AREA & WAKTU ---
area_geometry = ee.Geometry.Rectangle([114.368739,-8.263853,114.382801,-8.243553]) # Ganti dengan lokasi file Anda
start_date = '2024-01-01'
end_date = '2024-12-31'
proj_metric = ee.Projection('EPSG:32750')

output_dir = "path/to/your/folder" # Ganti dengan lokasi file Anda
if not os.path.exists(output_dir): os.makedirs(output_dir)

# --- 3. FUNGSI ---
def mask_clouds_scl(image):
    scl = image.select('SCL')
    mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
    return image.updateMask(mask).divide(10000)

def add_indices(image):
    ndwi = image.normalizedDifference(['B3', 'B8']).rename('NDWI')
    return image.addBands(ndwi)

def z_score_cleaning(collection, geometry):
    mean = collection.select('NDWI').mean()
    std = collection.select('NDWI').reduce(ee.Reducer.stdDev())
    upper_limit = mean.add(std.multiply(2))
    max_ndwi = collection.select('NDWI').max()
    return max_ndwi.where(max_ndwi.gt(upper_limit), upper_limit).clip(geometry)

def calculate_shape_metrics(feature):
    geom = feature.geometry()
    area = geom.area(1, proj=proj_metric)
    perimeter = geom.perimeter(1, proj=proj_metric)
    lsi = perimeter.multiply(0.25).divide(area.sqrt())
    hull = geom.convexHull()
    rpoc = perimeter.divide(hull.perimeter(1, proj=proj_metric))
    return feature.set({'area_m2': area, 'LSI': lsi, 'RPOC': rpoc})

def validate_with_dry_radar(feature, dry_radar_img):
    """Cek apakah poligon ini basah saat kemarau?"""
    val = dry_radar_img.reduceRegion(
        reducer=ee.Reducer.median(), geometry=feature.geometry(), scale=10, maxPixels=1e9
    ).get('VV')
    return feature.set('dry_vv', val)

def calculate_crop_overlap(feature):
    esa = ee.ImageCollection("ESA/WorldCover/v100").first()
    crop_pct = esa.eq(40).reduceRegion(
        reducer=ee.Reducer.mean(), geometry=feature.geometry(), scale=10, maxPixels=1e9
    ).get('Map')
    return feature.set('crop_pct', crop_pct)

# --- 4. PRE-PROCESSING (DATA TAHUNAN & KEMARAU) ---

print("1. Menyiapkan Sentinel-2 (Optik Tahunan)...")
s2_col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') 
        .filterBounds(area_geometry)
        .filterDate(start_date, end_date)
        .map(mask_clouds_scl)
        .map(add_indices))
ndwi_clean = z_score_cleaning(s2_col, area_geometry)

print("2. Menyiapkan Sentinel-1 (Radar Tahunan & Kemarau)...")
s1_col = (ee.ImageCollection('COPERNICUS/S1_GRD')
        .filterBounds(area_geometry)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
        .filter(ee.Filter.eq('instrumentMode', 'IW')))

# A. Radar Tahunan (Untuk Seeding/Deteksi Awal)
s1_annual = s1_col.select('VV').median().clip(area_geometry).focal_median(15, 'circle', 'meters')

# B. Radar Kemarau (Agustus-Oktober) - UNTUK VALIDASI ANTI SAWAH
s1_dry = (s1_col.filterDate('2024-08-01', '2024-10-31')
        .select('VV').median().clip(area_geometry).focal_median(10, 'circle', 'meters'))

# --- 5. HYBRID SEEDING (MENANGKAP SEMUA POTENSI AIR) ---
print("3. Membuat Masker Bibit (Optik + Radar Tahunan)...")

# Syarat 1: Air Optik (Jernih)
mask_optik = ndwi_clean.gte(0)
# Syarat 2: Air Radar Tahunan (Keruh/Gelap)
mask_radar = s1_annual.lt(-13.5)

# Gabung (Union) -> Menangkap Tambak + Sawah Basah
hybrid_mask = mask_optik.Or(mask_radar).selfMask().rename('WaterBinary')
hybrid_mask = hybrid_mask.updateMask(hybrid_mask.connectedPixelCount(10, True).gte(10))

# --- 6. SEGMENTASI SOAP ---
print("4. Segmentasi Iteratif (Memotong Poligon)...")

current_image = ndwi_clean
current_mask = hybrid_mask
accumulated_edges = ee.Image.constant(0)
valid_ponds_list = []

for i in range(3):
    print(f"   Iterasi ke-{i+1}...")
    kernel_radius = 1.5 + (i * 0.5) 
    morph = current_image.focal_min(radius=kernel_radius, kernelType='square', units='pixels')
    canny = ee.Algorithms.CannyEdgeDetector(image=morph, threshold=0.1, sigma=1)
    accumulated_edges = accumulated_edges.max(canny)
    segments = current_mask.where(accumulated_edges.eq(1), 0).selfMask()
    
    vectors = segments.reduceToVectors(
        geometry=area_geometry, scale=10, maxPixels=1e9, eightConnected=False, crs=proj_metric
    )
    
    # Filter Bentuk Awal
    processed = vectors.map(calculate_shape_metrics).filter(ee.Filter.And(
        ee.Filter.lte('LSI', 3.0),
        ee.Filter.lte('RPOC', 1.8)
    ))
    valid_ponds_list.append(processed)

# --- 7. FILTERING FINAL (ANTI-SAWAH) ---
print("5. Validasi Akhir (Anti-Sawah dgn Radar Kemarau)...")
candidates = ee.FeatureCollection(valid_ponds_list).flatten()

# A. Filter Luas
candidates = candidates.filter(ee.Filter.And(
    ee.Filter.gte('area_m2', 300), 
    ee.Filter.lte('area_m2', 500000)
))

# B. Filter Validasi Kemarau (Rice Killer)
candidates = candidates.map(lambda f: validate_with_dry_radar(f, s1_dry))

# LOGIKA KUNCI:
# Poligon harus GELAP (< -13 dB) saat Musim Kemarau.
# Jika Terang (> -13 dB), berarti tanah kering (Sawah/Tanah Kosong) -> BUANG!
final_result = candidates.filter(ee.Filter.lte('dry_vv', -13))

# C. Filter Cropland (Backup)
final_result = final_result.map(calculate_crop_overlap).filter(ee.Filter.lt('crop_pct', 0.5))

# D. Filter Tetangga
spatial_filter = ee.Filter.withinDistance(100, '.geo', None, '.geo')
join = ee.Join.saveAll('neighbors')
obj_with_neighbors = join.apply(final_result, final_result, spatial_filter)
final_result = obj_with_neighbors.map(lambda f: f.set('near_num', ee.List(f.get('neighbors')).size())).filter(ee.Filter.gte('near_num', 2))

# Buffer
final_result = final_result.map(lambda f: f.buffer(2))

print(f"SELESAI. Total Tambak Valid: {final_result.size().getInfo()}")

# --- 8. VISUALISASI ---
Map = geemap.Map()
Map.centerObject(area_geometry, 14)

# Layer 1: RGB
Map.addLayer(s2_col.median().clip(area_geometry).visualize(min=0, max=0.3, bands=['B4','B3','B2']), {}, 'S2 RGB')

# Layer 2: Radar Kemarau (Perhatikan Sawah Putih, Tambak Hitam)
Map.addLayer(s1_dry, {'min':-25, 'max':0}, 'S1 Dry Season (Validator)')

# Layer 3: Hasil Deteksi
Map.addLayer(final_result, {'color': 'red', 'width': 2}, 'Deteksi Tambak (Hybrid + AntiSawah)')

# --- 9. EKSPOR ---
try:
    print("Mengekspor...")
    out_shp = os.path.join(output_dir, 'nama_output_file.shp') #Ganti nama output file
    geemap.ee_to_shp(final_result.select(['area_m2', 'LSI', 'dry_vv']), filename=out_shp)
    print(f"SHP Tersimpan: {out_shp}")
except Exception as e:
    print(f"Error Ekspor: {e}")

Map