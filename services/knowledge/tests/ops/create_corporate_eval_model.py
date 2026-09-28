"""Create a tiny offline BoW fixture. Not a production embedding model."""
import os

if os.environ.get("VEDOMO_EVAL_FIXTURE") != "synthetic-only":
    raise RuntimeError("refusing to create fixture outside the isolated eval run")

from sentence_transformers import SentenceTransformer
from sentence_transformers.sentence_transformer.modules import BoW

vocabulary = """
alphadrive x120 x220 safesense s7 артикул напряжение питание 220 240 380 415
24 20 28 зазор монтаж прошивка гарантия ip20 ip54 ip67 температура e07 e12
cn3 cn5 alphalink двигатель сеть защита установка совместимость диагностика
""".split()
SentenceTransformer(modules=[BoW(vocab=vocabulary)], device="cpu").save_pretrained(
    "/models/corporate-bow", create_model_card=False
)
print("Created synthetic offline BoW model; not a BGE quality benchmark")
