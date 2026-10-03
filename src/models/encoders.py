"""
Phase 4: Modality-Specific Temporal Convolutional Encoders
1D Dilated Convolutional Networks for raw ECG and PPG waveform feature extraction.
"""

import torch
import torch.nn as nn
from typing import Tuple

class DilatedConvBlock(nn.Module):
    """
    1D Dilated Convolutional Block with LayerNorm, GELU, and Residual Connection.
    """
    def __init__(self, channels: int, dilation: int, kernel_size: int = 5):
        super().__init__()
        padding = (kernel_size - 1) * dilation // 2
        self.conv = nn.Conv1d(
            channels,
            channels,
            kernel_size=kernel_size,
            dilation=dilation,
            padding=padding
        )
        self.norm = nn.BatchNorm1d(channels)
        self.act = nn.GELU()
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        res = x
        out = self.conv(x)
        out = self.norm(out)
        out = self.act(out)
        return out + res

class WaveformEncoder(nn.Module):
    """
    Encodes raw 1D physiological time-series (e.g. ECG or PPG) into dense temporal embeddings.
    """
    def __init__(
        self,
        in_channels: int = 1,
        hidden_dim: int = 32,
        out_dim: int = 64,
        dilations: Tuple[int, ...] = (1, 2, 4, 8)
    ):
        super().__init__()
        self.input_proj = nn.Sequential(
            nn.Conv1d(in_channels, hidden_dim, kernel_size=7, stride=2, padding=3),
            nn.BatchNorm1d(hidden_dim),
            nn.GELU()
        )
        
        self.blocks = nn.ModuleList([
            DilatedConvBlock(hidden_dim, dilation=d) for d in dilations
        ])
        
        self.out_proj = nn.Sequential(
            nn.Conv1d(hidden_dim, out_dim, kernel_size=3, padding=1),
            nn.BatchNorm1d(out_dim),
            nn.GELU()
        )
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Input: (batch_size, 1, sequence_length)
        Output: (batch_size, out_dim, sequence_length // 2)
        """
        h = self.input_proj(x)
        for block in self.blocks:
            h = block(h)
        return self.out_proj(h)
