"""Load the same saved 2D observations into either replay page's timeline."""
import hashlib
from pathlib import Path

import pandas as pd

from Src.analysis.analysis_io import atomic_json, read_optional_json


def pose_timeline(folder, names):
    # Explicit per-camera metadata wins when single- and paired-video analyses
    # have both been saved. Their event files can describe different timelines.
    name = names[0]
    try:
        metadata = read_optional_json(Path(folder) / f'{name}_pose_timeline.json', {})
        pose_path = Path(folder) / f'{name}.json'
        if (metadata and pose_path.is_file()
                and metadata.get('pose_sha256') == hashlib.sha256(pose_path.read_bytes()).hexdigest()):
            return metadata
    except (OSError, ValueError, AttributeError):
        pass
    for name in dict.fromkeys(names):
        try:
            data = read_optional_json(Path(folder) / f'{name}_BRandRapsodo.json', {})
            meta = data.get('Analysis', {})
            if meta.get('pose_frame_number_space') == 'playback_timeline':
                return {**meta, 'video_name': data.get('video_name'),
                        'video_name_2': data.get('video_name_2')}
        except (OSError, ValueError, AttributeError):
            continue
    return {}


def save_pose_timeline(folder, name, first_name, second_name, offset):
    path = Path(folder) / f'{name}.json'
    atomic_json(Path(folder) / f'{name}_pose_timeline.json', {
        'pose_frame_number_space': 'playback_timeline', 'video_name': first_name,
        'video_name_2': second_name, 'frame_offset': offset,
        'pose_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    })


def pose_input_signature(folder, names):
    """Invalidate reconstructed results after the source 2D observations change."""
    signature = {}
    for name in dict.fromkeys(names):
        path = Path(folder) / f'{name}.json'
        meta = pose_timeline(folder, [name, *names])
        signature[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
                           'offset': saved_pose_offset(meta, name)}
    return signature


def saved_pose_offset(metadata, name):
    # A single-video recording uses one source for both views; its offset is 0.
    if (metadata and metadata.get('video_name_2') == name
            and metadata.get('video_name') != name):
        return int(metadata.get('frame_offset', 0))
    return 0


def remap_pose_rows(rows, metadata, name, offset, source_count, total_frames):
    if not isinstance(rows, list):
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if df.empty or not {'frame_number', 'person_id', 'keypoints'} <= set(df.columns):
        return pd.DataFrame()
    source_frame = df.frame_number - saved_pose_offset(metadata, name)
    df['frame_number'] = source_frame + offset
    # Boundary-clipped duplicates do not represent observations of that frame.
    return df[source_frame.between(0, source_count - 1)
              & df.frame_number.between(0, total_frames - 1)].reset_index(drop=True)
