"""Reproducible synthetic instances and hand-verifiable reference cases."""

from km_quantum.instances.generator import GeneratorConfig, generate_instance
from km_quantum.instances.hand_cases import hand_cases

__all__ = ["GeneratorConfig", "generate_instance", "hand_cases"]
