"""Diagnose why an image yields chars=0 — runs predict() WITHOUT swallowing errors."""
import sys
import numpy as np
from PIL import Image
from paddleocr import PaddleOCR

path = sys.argv[1]
with Image.open(path) as im:
    im = im.convert("RGB")
    print(f"image size: {im.size} (w x h)")
    base = np.asarray(im)[:, :, ::-1].copy()  # BGR
    big = np.asarray(im.resize((im.size[0] * 2, im.size[1] * 2)))[:, :, ::-1].copy()


def run(label, lang, arr, textline):
    print(f"\n=== {label} (lang={lang}, textline={textline}, shape={arr.shape}) ===")
    try:
        eng = PaddleOCR(
            lang=lang,
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=textline,
        )
        res = eng.predict(arr)
        r0 = res[0]
        texts = r0["rec_texts"]
        scores = r0["rec_scores"]
        print(f"detections: {len(texts)}")
        for t, s in zip(texts, scores):
            print(f"  [{s:.3f}] {t!r}")
    except Exception as e:
        import traceback
        print("RAISED:", type(e).__name__, e)
        traceback.print_exc()


run("ch / original", "ch", base, False)
run("en / original", "en", base, False)
run("ch / 2x upscale", "ch", big, False)
run("ch / textline ON", "ch", base, True)
