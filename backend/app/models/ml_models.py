import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from typing import Dict, Any, List, Optional
import os

class TremorMLClassifier:
    """
    Experimental Machine Learning demonstration layer.
    Trained strictly on synthetic parameter distributions for pipeline verification.
    Not validated against clinical patient cohort data.
    """
    def __init__(self):
        self.scaler = StandardScaler()
        self.rf_model = RandomForestClassifier(n_estimators=100, random_state=42)
        self.is_trained = False
        self._train_default_benchmark_model()

    def _generate_synthetic_benchmark_dataset(self, n_samples: int = 1500):
        # Use isolated local RandomState to avoid global side-effects
        rng = np.random.RandomState(42)
        half = n_samples // 2
        
        # 1. Synthetic Tremor Class (Class 1)
        dom_freq_pd = rng.normal(loc=5.1, scale=0.8, size=half)
        dom_freq_pd = np.clip(dom_freq_pd, 3.6, 7.4)
        peak_freq_pd = dom_freq_pd + rng.normal(0, 0.2, size=half)
        
        rms_pd = rng.exponential(scale=0.35, size=half) + 0.08
        rms_tremor_pd = rms_pd * rng.uniform(0.65, 0.95, size=half)
        
        ratio_pd = rng.beta(a=6.0, b=2.5, size=half)
        ratio_pd = np.clip(ratio_pd, 0.42, 0.98)
        
        entropy_pd = rng.normal(loc=0.38, scale=0.12, size=half)
        entropy_pd = np.clip(entropy_pd, 0.15, 0.65)
        
        zcr_pd = dom_freq_pd * 2.0 + rng.normal(0, 1.0, size=half)
        
        X_pd = np.column_stack([
            dom_freq_pd, peak_freq_pd, rms_pd, rms_tremor_pd, ratio_pd, entropy_pd, zcr_pd
        ])
        y_pd = np.ones(half)
        
        # 2. Synthetic Baseline/Voluntary Class (Class 0)
        dom_freq_norm = np.concatenate([
            rng.uniform(0.5, 2.5, size=half//2),
            rng.uniform(8.0, 15.0, size=half - half//2)
        ])
        peak_freq_norm = dom_freq_norm + rng.normal(0, 0.5, size=half)
        
        rms_norm = rng.exponential(scale=0.15, size=half) + 0.01
        rms_tremor_norm = rms_norm * rng.uniform(0.05, 0.30, size=half)
        
        ratio_norm = rng.beta(a=2.0, b=7.0, size=half)
        ratio_norm = np.clip(ratio_norm, 0.01, 0.36)
        
        entropy_norm = rng.normal(loc=0.78, scale=0.10, size=half)
        entropy_norm = np.clip(entropy_norm, 0.55, 0.98)
        
        zcr_norm = dom_freq_norm * 2.0 + rng.normal(0, 3.0, size=half)
        zcr_norm = np.clip(zcr_norm, 0.5, 30.0)
        
        X_norm = np.column_stack([
            dom_freq_norm, peak_freq_norm, rms_norm, rms_tremor_norm, ratio_norm, entropy_norm, zcr_norm
        ])
        y_norm = np.zeros(half)
        
        X = np.vstack([X_pd, X_norm])
        y = np.concatenate([y_pd, y_norm])
        
        indices = rng.permutation(len(y))
        return X[indices], y[indices]

    def _train_default_benchmark_model(self):
        X, y = self._generate_synthetic_benchmark_dataset(2000)
        X_scaled = self.scaler.fit_transform(X)
        self.rf_model.fit(X_scaled, y)
        self.is_trained = True

    def predict_confidence(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs model inference on extracted features.
        Explicitly marked as experimental synthetic classifier output.
        """
        if not self.is_trained:
            return {
                "probability": 0.0,
                "confidence_percent": 0.0,
                "ml_label": "NO_TREMOR",
                "is_synthetic_model": True
            }
            
        vector = np.array([[
            features.get("dominant_freq", 0.0),
            features.get("peak_freq_full", 0.0),
            features.get("rms_amp", 0.0),
            features.get("rms_amp_tremor", 0.0),
            features.get("band_power_ratio", 0.0),
            features.get("spectral_entropy", 0.0),
            features.get("zero_crossing_rate", 0.0)
        ]])
        
        vector_scaled = self.scaler.transform(vector)
        prob = float(self.rf_model.predict_proba(vector_scaled)[0, 1])
        
        # Experimental score classification
        if prob >= 0.70:
            label = "SYNTHETIC_MODEL_ELEVATED_PROBABILITY"
        elif prob >= 0.40:
            label = "SYNTHETIC_MODEL_INTERMEDIATE"
        else:
            label = "SYNTHETIC_MODEL_BASELINE"
            
        return {
            "probability": round(prob, 3),
            "confidence_percent": round(prob * 100.0, 1),
            "ml_label": label,
            "is_synthetic_model": True,
            "notes": "Trained on synthetic distribution; not a calibrated clinical diagnostic probability."
        }

ml_classifier = TremorMLClassifier()
