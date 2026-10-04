#!/usr/bin/env python3
from pathlib import Path
import ast
import sys

TARGET = Path("app/routers/marketplaces.py")
MARKER = "CHZ_RELEASED_ON_CANCEL"
HELPER = r