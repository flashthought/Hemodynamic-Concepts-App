"""
Cross-Modal Fusion and Latent Dynamical Representation
Fuses ECG and PPG embeddings, estimates latent state z(t) and effective parameters R, C.
"""

import torch
import torch.nn as nn
from typing import Dict, Tuple

class CrossModalAttentionFusion(nn.Module):
    """
    Cross-attention fusion between electrical cardiac timing (ECG)
    and peripheral vascular pulse waves (PPG).
    """
    def __init__(self, feature_dim: int = 64, num_heads: int = 4):
        super().__init__()
        self.mha = nn.MultiheadAttention(embed_dim=feature_dim, num_heads=num_heads, batch_first=True)
        self.norm = nn.LayerNorm(feature_dim)
        self.proj = nn.Linear(feature_dim * 2, feature_dim)
        
    def forward(self, h_ecg: torch.Tensor, h_ppg: torch.Tensor) -> torch.Tensor:
        """
        Inputs: (batch_size, channels, time) -> transpose to (batch, time, channels)
        """
        x_ecg = h_ecg.transpose(1, 2)
        x_ppg = h_ppg.transpose(1, 2)
        
        # PPG queries ECG to identify pulse arrival time relative to electrical depolarization
        attn_out, _ = self.mha(query=x_ppg, key=x_ecg, value=x_ecg)
        fused = torch.cat([x_ppg, self.norm(x_ppg + attn_out)], dim=-1)
        return self.proj(fused).transpose(1, 2) # (batch, channels, time)

class LatentHemodynamicModel(nn.Module):
    """
    Complete Physics-Informed Latent State and Trajectory Forecasting Architecture.
    """
    def __init__(
        self,
        feature_dim: int = 64,
        latent_dim: int = 6,
        forecast_horizon: int = 15,
        R_baseline: float = 1.0,
        C_baseline: float = 1.2
    ):
        super().__init__()
        self.R_baseline = R_baseline
        self.C_baseline = C_baseline
        
        # Latent state encoder: compresses fused features into compact state z(t)
        self.latent_head = nn.Sequential(
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),
            nn.Linear(feature_dim, 32),
            nn.GELU(),
            nn.Linear(32, latent_dim)
        )
        
        # Effective Parameter Head (Log-parameterized to strictly guarantee R > 0, C > 0)
        self.param_head = nn.Sequential(
            nn.Linear(latent_dim + 1, 16),
            nn.GELU(),
            nn.Linear(16, 2) # [log_r_ratio, log_c_ratio]
        )
        
        # Forecast Decoder: short-horizon MAP trajectory
        self.forecast_head = nn.Sequential(
            nn.Linear(latent_dim + 3, 32),
            nn.GELU(),
            nn.Linear(32, forecast_horizon)
        )
        
        # Uncertainty head: outputs variance estimate
        self.unc_head = nn.Sequential(
            nn.Linear(32, forecast_horizon),
            nn.Softplus()
        )
        
        # --- PHYSIOLOGICAL INITIALIZATION ---
        # Initialize output forecast bias to baseline adult MAP (85.0 mmHg)
        # and small weights so the network starts predicting close to baseline immediately
        nn.init.constant_(self.forecast_head[2].bias, 85.0)
        nn.init.normal_(self.forecast_head[2].weight, std=0.01)
        
        # Initialize parameter head around ratio = 1.0 (log = 0.0)
        nn.init.zeros_(self.param_head[2].bias)
        nn.init.normal_(self.param_head[2].weight, std=0.02)

    def forward(
        self,
        h_fused: torch.Tensor,
        u_interventions: torch.Tensor = None
    ) -> Dict[str, torch.Tensor]:
        """
        Returns:
          - z: latent state
          - R: estimated resistance
          - C: estimated compliance
          - forecast: predicted trajectory
          - uncertainty: predicted variance
        """
        z = self.latent_head(h_fused) # (batch, latent_dim)

        if u_interventions is None:
            u_summary = torch.zeros(z.shape[0], 1, device=z.device, dtype=z.dtype)
        elif u_interventions.ndim == 3:
            u_summary = torch.mean(u_interventions, dim=-1)
        elif u_interventions.ndim == 2:
            u_summary = torch.mean(u_interventions, dim=-1, keepdim=True)
        else:
            u_summary = u_interventions.view(z.shape[0], 1)
        
        # Log-parameterization: R = R_0 * exp(r), C = C_0 * exp(c)
        z_with_u = torch.cat([z, u_summary], dim=-1)
        log_params = self.param_head(z_with_u)
        r_mult = torch.exp(torch.clamp(log_params[:, 0:1], -1.5, 1.5))
        c_mult = torch.exp(torch.clamp(log_params[:, 1:2], -1.5, 1.5))
        
        R_est = self.R_baseline * r_mult
        C_est = self.C_baseline * c_mult
        
        # Concatenate latent state and estimated physical parameters
        state_repr = torch.cat([z, R_est, C_est, u_summary], dim=-1)
        hidden = nn.functional.gelu(self.forecast_head[0](state_repr))
        forecast = self.forecast_head[2](hidden)
        unc = self.unc_head(hidden) + 1.0 # baseline floor
        
        return {
            'z': z,
            'R': R_est,
            'C': C_est,
            'u_summary': u_summary,
            'forecast': forecast,
            'uncertainty': unc
        }
