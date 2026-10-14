import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import torch
import torch.nn.functional as F
from mmpose.apis import init_model
from mmpose.utils import register_all_modules
import os
from Src.paths import SRC_DIR, DB_DIR

# --- 核心修正：Monkey Patch Scaled Dot Product Attention ---
# def manual_sdpa(query, key, value, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None):
#     # 這是一般的 Attention 數學實現，ONNX 100% 支援
#     if scale is None:
#         scale = query.size(-1) ** 0.5
#     attn_weight = torch.matmul(query, key.transpose(-2, -1)) / scale
#     if attn_mask is not None:
#         attn_weight += attn_mask
#     attn_weight = F.softmax(attn_weight, dim=-1)
#     return torch.matmul(attn_weight, value)

# # 強制替換掉 PyTorch 內建的 SDPA
# torch.nn.functional.scaled_dot_product_attention = manual_sdpa
# -------------------------------------------------------

register_all_modules()

config_file = str(SRC_DIR / 'mmpose_main/configs/body_2d_keypoint/topdown_heatmap/haple/ViTPose_base_simple_halpe_256x192.py')
checkpoint_file = str(DB_DIR / 'pretrain/epoch_210.pth')
output_onnx = "ViTPose_26kpts.onnx"

print("正在載入模型結構與權重...")
model = init_model(config_file, checkpoint_file, device='cpu')
model.eval()

# 根據你的 256x192 設定輸入尺寸
dummy_input = torch.randn(1, 3, 256, 192)

print(f"正在匯出至 {output_onnx} (手動解析 Attention)...")
try:
    torch.onnx.export(
        model, 
        dummy_input, 
        output_onnx, 
        opset_version=17,  # 使用 11 或 12 即可
        do_constant_folding=True,
        input_names=['input'], 
        output_names=['output'],
        dynamic_axes={'input': {0: 'batch_size'}, 'output': {0: 'batch_size'}}
    )
    print("✨ 轉換成功！現在 ONNX 已經能理解模型結構了。")
except Exception as e:
    print(f"❌ 轉換仍然失敗: {e}")