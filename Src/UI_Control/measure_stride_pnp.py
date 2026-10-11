"""Metric stride measurement using chessboard PnP and stereo refinement."""
from cv_utils.stride_workflow import entrypoint

if __name__ == "__main__":
    entrypoint("pnp")
