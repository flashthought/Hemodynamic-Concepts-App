"""
Physics-Informed Loss Function
Enforces Windkessel cardiovascular differential equations alongside observational losses.
"""

import torch
import torch.nn as nn
from typing import Dict

class PhysicsInformedLoss(nn.Module):
    """
    Multi-objective loss combining:
    1. L_obs: Trajectory forecasting error (Huber / SmoothL1 loss)
    2. L_phys: Windkessel differential equation residual penalty
    3. L_param: Parameter consistency and prior bounds
    """
    def __init__(
        self,
        w_obs: float = 1.0,
        w_phys: float = 0.1,
        w_param: float = 0.5,
        w_param_var: float = 0.0,
        w_pulse: float = 0.0,
        r_scale: float = 1.0,
        c_scale: float = 1.0,
        P_v: float = 4.0
    ):
        super().__init__()
        self.w_obs = w_obs
        self.w_phys = w_phys
        self.w_param = w_param
        self.w_param_var = w_param_var
        self.w_pulse = w_pulse
        self.r_scale = max(float(r_scale), 1e-6)
        self.c_scale = max(float(c_scale), 1e-6)
        self.P_v = P_v
        self.huber = nn.SmoothL1Loss(beta=2.0)

    def forward(
        self,
        preds: Dict[str, torch.Tensor],
        targets: torch.Tensor,
        r_true: torch.Tensor = None,
        c_true: torch.Tensor = None,
        dt: float = 1.0
    ) -> Dict[str, torch.Tensor]:
        """
        preds: output dictionary from LatentHemodynamicModel ('forecast', 'R', 'C', 'uncertainty')
        targets: ground truth future MAP trajectory (batch, horizon)
        r_true: optional true systemic vascular resistance for supervision
        c_true: optional true arterial compliance for supervision
        """
        forecast = preds['forecast']
        R_est = preds['R']
        C_est = preds['C']
        
        # 1. Observational loss (SmoothL1 on mmHg trajectory)
        l_obs = self.huber(forecast, targets)

        # 1b. Pulse-pressure loss: helps compliance learning by preserving
        # waveform amplitude, not just average pressure level.
        if forecast.shape[1] >= 2 and self.w_pulse > 0:
            pred_pp = forecast.max(dim=1).values - forecast.min(dim=1).values
            true_pp = targets.max(dim=1).values - targets.min(dim=1).values
            l_pulse = self.huber(pred_pp, true_pp)
        else:
            l_pulse = torch.tensor(0.0, device=forecast.device)
        
        # 2. Physics loss (Windkessel differential equation residual)
        # Flow balance: C * dP/dt = Q_in - (P - P_v) / R
        if forecast.shape[1] >= 2 and self.w_phys > 0:
            dp_dt = (forecast[:, 1:] - forecast[:, :-1]) / dt
            p_mid = 0.5 * (forecast[:, 1:] + forecast[:, :-1])
            
            # Mean aortic inflow matches baseline perfusion (~76 mL/s for 75 bpm)
            q_in = (85.0 - self.P_v) / 1.0 
            drain = (p_mid - self.P_v) / (R_est + 1e-4)
            physics_residual = C_est * dp_dt - (q_in - drain)
            l_phys = torch.mean(torch.abs(physics_residual))
        else:
            l_phys = torch.tensor(0.0, device=forecast.device)
            
        # 3. Parameter loss (Auxiliary supervision when r_true is given in training)
        if r_true is not None and c_true is not None and self.w_param > 0:
            r_target = r_true.unsqueeze(-1)
            c_target = c_true.unsqueeze(-1)
            l_param = (
                torch.mean(torch.abs(R_est - r_target) / self.r_scale)
                + torch.mean(torch.abs(C_est - c_target) / self.c_scale)
            )
        elif r_true is not None and self.w_param > 0:
            r_target = r_true.unsqueeze(-1)
            l_param = torch.mean(torch.abs(R_est - r_target) / self.r_scale)
        else:
            # Soft physiological prior (penalize extreme deviations outside [0.5, 1.8])
            l_param = torch.mean((R_est - 1.0) ** 2) + torch.mean((C_est - 1.2) ** 2)

        if (
            r_true is not None
            and c_true is not None
            and self.w_param_var > 0
            and R_est.numel() > 1
            and C_est.numel() > 1
        ):
            r_var_target = torch.var(r_true.float(), unbiased=False)
            c_var_target = torch.var(c_true.float(), unbiased=False)
            r_var_pred = torch.var(R_est.reshape(-1), unbiased=False)
            c_var_pred = torch.var(C_est.reshape(-1), unbiased=False)
            l_param_var = torch.abs(r_var_pred - r_var_target) / (r_var_target + 1e-6)
            l_param_var = l_param_var + torch.abs(c_var_pred - c_var_target) / (c_var_target + 1e-6)
        else:
            l_param_var = torch.tensor(0.0, device=forecast.device)
        
        total_loss = (
            self.w_obs * l_obs
            + self.w_phys * l_phys
            + self.w_param * l_param
            + self.w_param_var * l_param_var
            + self.w_pulse * l_pulse
        )
        
        return {
            'total_loss': total_loss,
            'l_obs': l_obs,
            'l_phys': l_phys,
            'l_param': l_param,
            'l_param_var': l_param_var,
            'l_pulse': l_pulse,
            'forecast_mae': torch.mean(torch.abs(forecast - targets))
        }
