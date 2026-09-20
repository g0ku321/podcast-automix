#!/usr/bin/env python3
import sys
import os

# Ensure local package directory is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from podcast_automix.automix import main

if __name__ == "__main__":
    main()
