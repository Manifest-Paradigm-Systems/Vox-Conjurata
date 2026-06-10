import cv2
import numpy as np
import logging
import json
from pathlib import Path
from PIL import Image
import io

logger = logging.getLogger("vox-map-geometry")

class MapGeometryEngine:
    def __init__(self):
        pass

    def analyze_map(self, image_bytes: bytes, scene_id: str):
        """Analyzes a battlemap image to detect walls using OpenCV."""
        # Convert bytes to OpenCV image
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            logger.error("Failed to decode image for geometry analysis.")
            return None

        height, width = img.shape[:2]
        logger.info(f"Analyzing map: {width}x{height}")

        # 1. Edge Detection
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        # Canny edge detection
        edges = cv2.Canny(blurred, 50, 150, apertureSize=3)

        # 2. Line Detection (Hough Transform)
        # We use Probabilistic Hough Transform for segments
        lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=100, 
                                minLineLength=50, maxLineGap=10)

        foundry_walls = []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                # Convert to Foundry format (normalized or absolute?)
                # Foundry usually uses absolute coordinates on the canvas
                foundry_walls.append({
                    "c": [int(x1), int(y1), int(x2), int(y2)],
                    "move": 1, # Normal wall
                    "sense": 1,
                    "door": 0
                })

        logger.info(f"Detected {len(foundry_walls)} wall segments via OpenCV.")

        return {
            "sceneId": scene_id,
            "walls": foundry_walls,
            "lights": [], # To be filled by Vision Reader
            "doors": []   # To be filled by Vision Reader
        }

    async def merge_vision_predictions(self, geometry_data: dict, vision_objects: list):
        """Merges OpenCV wall detections with Vision Reader object detections (doors, lights)."""
        # vision_objects should contain objects with labels and coordinates
        for obj in vision_objects:
            if obj["label"] == "door":
                # Convert bounding box to wall segment
                # Simplified: horizontal or vertical line based on aspect ratio
                x, y, w, h = obj["bbox"]
                if w > h: # Horizontal door
                    geometry_data["walls"].append({
                        "c": [int(x), int(y + h/2), int(x + w), int(y + h/2)],
                        "door": 1 # Door
                    })
            elif obj["label"] == "light":
                x, y, w, h = obj["bbox"]
                geometry_data["lights"].append({
                    "x": int(x + w/2),
                    "y": int(y + h/2),
                    "config": {"color": "#ffffff", "dim": 20, "bright": 10}
                })
        return geometry_data

map_geometry_engine = MapGeometryEngine()
