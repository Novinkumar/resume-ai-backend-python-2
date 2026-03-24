# ocr_service.py

import io
from google.cloud import vision
from PIL import Image
import base64

class OCRService:
    def __init__(self, credentials_path='google-vision-key.json'):
        """Initialize Google Vision client"""
        self.client = vision.ImageAnnotatorClient()
        self.credentials_path = credentials_path

    def extract_text_from_image(self, image_path):
        """Extract text from image file"""
        try:
            with open(image_path, 'rb') as image_file:
                content = image_file.read()

            image = vision.Image(content=content)
            response = self.client.document_text_detection(image=image)

            # Extract full text
            full_text = response.full_text_annotation.text if response.full_text_annotation else ""

            return {
                "success": True,
                "text": full_text,
                "confidence": self._calculate_confidence(response),
                "error": None
            }

        except Exception as e:
            return {
                "success": False,
                "text": "",
                "confidence": 0,
                "error": str(e)
            }

    def extract_text_from_base64(self, base64_string):
        """Extract text from base64 encoded image"""
        try:
            # Decode base64
            image_data = base64.b64decode(base64_string)

            image = vision.Image(content=image_data)
            response = self.client.document_text_detection(image=image)

            full_text = response.full_text_annotation.text if response.full_text_annotation else ""

            return {
                "success": True,
                "text": full_text,
                "confidence": self._calculate_confidence(response),
                "error": None
            }

        except Exception as e:
            return {
                "success": False,
                "text": "",
                "confidence": 0,
                "error": str(e)
            }

    def _calculate_confidence(self, response):
        """Calculate average confidence from response"""
        if not response.text_annotations:
            return 0

        confidences = [
            annotation.confidence
            for annotation in response.text_annotations
            if annotation.confidence > 0
        ]

        if not confidences:
            return 0.85  # Default confidence

        return sum(confidences) / len(confidences)

# Create singleton instance
ocr_service = OCRService()