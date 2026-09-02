"""onnxocr 适配器，对齐 ppocr-onnx 的 OCR 调用接口。"""
from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import onnxruntime
from ppocronnx.predict_system import BoxedResult

from module.logger import logger


class OnnxOcrTextSystem:
    """将 onnxocr 封装为现有 OCR RPC 所需的接口。"""

    def __init__(self, *, use_gpu: bool = False, gpu_id: int = 0, cpu_threads: int = 10):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        providers = onnxruntime.get_available_providers()
        enable_gpu = use_gpu and "CUDAExecutionProvider" in providers
        if use_gpu and not enable_gpu:
            logger.warning("onnxocr 请求 GPU 推理，但 CUDAExecutionProvider 不可用，回退 CPU。")

        self._model = ONNXPaddleOcr(
            use_gpu=enable_gpu,
            gpu_id=gpu_id,
            cpu_threads=cpu_threads,
            use_angle_cls=False,
            drop_score=0.5,
        )
        logger.info(
            "OCR backend onnxocr initialized (provider=%s, gpu_id=%s)",
            "CUDA" if enable_gpu else "CPU",
            gpu_id,
        )

    def ocr_single_line(self, image: np.ndarray):
        results = self._model.text_recognizer([image])
        if not results:
            return "", 0.0
        text, score = results[0]
        return text, float(score)

    @contextmanager
    def _detection_options(self, drop_score, unclip_ratio, box_thresh):
        old_drop_score = self._model.drop_score
        detector = self._model.text_detector
        postprocess = detector.postprocess_op
        old_unclip_ratio = postprocess.unclip_ratio
        old_box_thresh = postprocess.box_thresh
        try:
            self._model.drop_score = drop_score
            if unclip_ratio is not None:
                postprocess.unclip_ratio = unclip_ratio
            if box_thresh is not None:
                postprocess.box_thresh = box_thresh
            yield
        finally:
            self._model.drop_score = old_drop_score
            postprocess.unclip_ratio = old_unclip_ratio
            postprocess.box_thresh = old_box_thresh

    def detect_and_ocr(
        self,
        image: np.ndarray,
        drop_score: float = 0.5,
        unclip_ratio: float | None = None,
        box_thresh: float | None = None,
    ):
        with self._detection_options(drop_score, unclip_ratio, box_thresh):
            ocr_result = self._model.ocr(image, det=True, rec=True, cls=False)

        if not ocr_result:
            return []
        results = []
        for box, (text, score) in ocr_result[0]:
            if score >= drop_score:
                results.append(BoxedResult(np.asarray(box), None, text, float(score)))
        return results
