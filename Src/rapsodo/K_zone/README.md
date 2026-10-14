

## Strike Zone / YOLO Ball Detection (Left Original, Right Detection)

Use the strike-zone tracker to render side-by-side output:

- Left panel: original input frame
- Right panel: YOLO ball detection result + `K_Zone_ROI` and `K_Zone` overlays

```bash
uv run python mp4_strikezone_tracker.py input.mp4 --roi-file roi_presets_2.json --search-zone-name K_Zone_ROI --strike-zone-name K_Zone --save-video strikezone_preview.mp4 --output-csv strikezone_points.csv
```

Useful options:

- `--yolo-model yolov8n.pt` (or custom YOLO model path)
- `--yolo-conf 0.10` (lower to `0.01` for tiny/weak detections)
- `--display-scale 1.0`

CSV columns:

- `frame`
- `dot_x`
- `dot_y`
- `rel_x`
- `rel_y`
- `inside_zone` (`1` if the ball is inside `K_Zone`, else `0`)

Notes:

- Default search ROI is `K_Zone_ROI`
- Default strike-zone ROI is `K_Zone`
- Requires Ultralytics YOLO package (`uv add ultralytics`)

# Key Funtion

- `detect_ball_yolo_in_search_roi`:use yolo for ball detection for given region

