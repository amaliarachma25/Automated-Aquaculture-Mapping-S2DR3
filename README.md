# Multi-Scale Aquaculture Pond Detection: From Hybrid GEE Analysis to High-Resolution S2DR3 (1m)

This repository contains the code implementation for an automated aquaculture pond detection system developed for the Banyuwangi coastal region. 

This project is an **adaptation and extension** of the Object-Oriented method proposed by [Li et al. (2023)](https://doi.org/10.3390/rs15030856), enhanced with **Sentinel-1 SAR integration** and scaled down for **1-meter Deep Resolution (S2DR3)** imagery using local Python processing.

S2DR3 Source : https://colab.research.google.com/drive/18phbwA1iYG5VDGN2WjK7WrWYi-FdCHJ5#scrollTo=XoCA_oizWi_g 

## 📌 Adaptation & Modifications
While the core geometric constraints are inspired by Li et al., this project introduces several key modifications to suit local conditions and high-resolution data:

1.  **Hybrid Sensor Integration (Script 01 & 02):**
    * *Original Method:* Relied solely on Optical Sentinel-2 images.
    * *Our Adaptation:* Integrated **Sentinel-1 SAR (Radar)** data to validate water presence during the dry season and overcome cloud cover issues frequent in tropical Indonesia.
2.  **Resolution Scaling (Script 03):**
    * *Original Method:* Designed for 10m Sentinel-2 imagery.
    * *Our Adaptation:* Adapted the **OBIA (Object-Based Image Analysis)** workflow for **1m S2DR3 (Sentinel-2 Deep Resolution)** imagery.
3.  **Advanced Smoothing Technique:**
    * *Innovation:* Implemented a **"Double Buffer Smoothing"** algorithm (+2m / -2m) to handle jagged pixel edges inherent in high-resolution raster data, ensuring the **Landscape Shape Index (LSI)** calculation remains valid for 1m pixels.

## 📂 Repository Structure

The code is divided into Cloud-based (Google Earth Engine) and Local (Python) processing:

### 1. `01_GEE_Hybrid_DrySeason.py` (Google Earth Engine)
* **Purpose:** Initial detection and validation using Radar.
* **Key Feature:** Uses Sentinel-1 backscatter thresholds (VV polarization) to verify water presence during the driest months, reducing false positives from wet rice paddies.

### 2. `02_GEE_Spatial_Temporal.py` (Google Earth Engine)
* **Purpose:** Temporal consistency analysis.
* **Key Feature:** Applies spatial neighbor filtering to detect pond clusters and eliminate isolated noise pixels.

### 3. `03_Local_S2DR3_HighRes.py` (Python / Local)
* **Purpose:** Fine-scale detection on 1m Resolution Imagery.
* **Key Feature:**
    * **Spectral Filtering:** NDWI (Water), NDVI (Vegetation exclusion), NIR (Roof/Settlement exclusion).
    * **Morphological Cleaning:** Large kernel opening to separate ponds from the sea.
    * **Geometric Filter:** Strict shape analysis (Area, LSI < 2.0, RPOC < 1.6).


Requirements (For Script 03)
To run the local Python script, ensure you have the following libraries installed:

**pip install rasterio geopandas shapely opencv-python numpy**




Reference & Citation
This project adapts the methodology described in:

Li, B.; Gong, A.; Chen, Z.; Pan, X.; Li, L.; Li, J.; Bao, W. An Object-Oriented Method for Extracting Single-Object Aquaculture Ponds from 10 m Resolution Sentinel-2 Images on Google Earth Engine. Remote Sens. 2023, 15, 856. https://doi.org/10.3390/rs15030856
