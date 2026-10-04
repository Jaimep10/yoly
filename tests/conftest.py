# pytest carga este archivo antes que cualquier prueba: matplotlib sin ventanas (Mac).
import os

os.environ["MPLBACKEND"] = "Agg"
