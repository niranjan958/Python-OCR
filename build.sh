#!/usr/bin/env bash
set -e

echo "Installing Tesseract OCR..."
sudo apt-get update -y
sudo apt-get install -y tesseract-ocr tesseract-ocr-hin tesseract-ocr-tam

echo "Installing Python packages..."
pip install -r requirements.txt

echo "Build complete!"
