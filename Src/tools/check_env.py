import torch
import ultralytics

print(f"PyTorch Version: {torch.__version__}")
print(f"CUDA Available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU Device: {torch.cuda.get_device_name(0)}")
    print(f"Compute Capability: {torch.cuda.get_device_capability(0)}")

print(f"Ultralytics Version: {ultralytics.__version__}")

# 測試 ONNX Runtime 是否抓到 GPU
import onnxruntime as ort
print(f"ONNX Providers: {ort.get_available_providers()}")