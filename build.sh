#!/usr/bin/env bash
set -e
 
echo "Installing Tesseract OCR..."
apt-get update -y
apt-get install -y tesseract-ocr tesseract-ocr-hin tesseract-ocr-tam
 
echo "Installing Python packages..."
pip install -r requirements.txt
 
echo "Build complete!"
