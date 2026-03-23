import pandas as pd
from typing import Optional
import warnings
from sdv.single_table import GaussianCopulaSynthesizer
from sdv.metadata import Metadata

def generate_synthetic_data(file_path: str, output_path: Optional[str], num_rows: int, seed: int = None):
    """
    Loads a CSV, trains an SDV GaussianCopula model, and saves synthetic data.
    """
    # Load data
    data = pd.read_csv(file_path)

    if seed is not None:
        import random
        import numpy as np
        random.seed(seed)
        np.random.seed(seed)
        try:
            import torch
            torch.manual_seed(seed)
        except ImportError:
            pass
    
    # Detect metadata
    metadata = Metadata.detect_from_dataframe(data=data)
    
    # Initialize and fit synthesizer
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"We strongly recommend saving the metadata using 'save_to_json'.*",
            category=UserWarning,
        )
        synthesizer = GaussianCopulaSynthesizer(metadata)
    
    # Note: Fitting process itself can have randomness, but for GaussianCopula 
    # the main reproducibility is in the sampling.
    synthesizer.fit(data)
    
    synthetic_data = synthesizer.sample(num_rows=num_rows)
    
    if output_path:
        synthetic_data.to_csv(output_path, index=False)
        return output_path
    return synthetic_data
