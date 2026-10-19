import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Tests never read the user's real config.py
os.environ["MESH_CONFIG"] = os.path.join(ROOT, "config.example.py")
