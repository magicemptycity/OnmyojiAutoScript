"""OCR 后端选择。

默认使用 ppocr-onnx；onnxocr 为可选后端，未安装时不影响默认流程。
"""
from __future__ import annotations


BACKEND_PPOCR_ONNX = "ppocr_onnx"
BACKEND_ONNXOCR = "onnxocr"


def normalize_ocr_backend(backend: str | None) -> str:
    value = (backend or BACKEND_PPOCR_ONNX).strip().lower().replace("-", "_")
    aliases = {
        "ppocr": BACKEND_PPOCR_ONNX,
        "ppocr_onnx": BACKEND_PPOCR_ONNX,
        "onnxocr": BACKEND_ONNXOCR,
        "onnx_paddleocr": BACKEND_ONNXOCR,
    }
    if value not in aliases:
        raise ValueError(
            f"Unknown OCR backend: {backend!r}; available: "
            f"{BACKEND_PPOCR_ONNX}, {BACKEND_ONNXOCR}"
        )
    return aliases[value]


def create_ocr_text_system(
    backend: str | None = None,
    *,
    use_gpu: bool = False,
    gpu_id: int = 0,
    cpu_threads: int = 10,
):
    """按配置创建 OCR 模型，可选依赖只在被选中时导入。"""
    selected = normalize_ocr_backend(backend)
    if selected == BACKEND_PPOCR_ONNX:
        from module.ocr.ppocr import TextSystem

        return TextSystem()

    try:
        from module.ocr.onnxocr_backend import OnnxOcrTextSystem
    except ModuleNotFoundError as exc:
        if exc.name in {"onnxocr", "onnxruntime"}:
            raise RuntimeError(
                "OCR 后端 onnxocr 未安装。请先安装 requirements-ocr-onnx.txt，"
                "或将 OcrBackend 改回 ppocr_onnx。"
            ) from exc
        raise

    return OnnxOcrTextSystem(
        use_gpu=use_gpu,
        gpu_id=gpu_id,
        cpu_threads=cpu_threads,
    )
