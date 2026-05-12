"""
Benchmark cross-loja: busca o mesmo produto em lojas externas (não-marketplace)
pra detectar quando old_price declarado pela loja origem é inflado.

Hoje só Kabum (única loja BR que entrega busca via JSON-LD server-side em
httpx puro, sem proxy/Playwright). Arquitetura aceita N lojas no futuro.
"""
from .aggregator import benchmark_lookup, classify_real_discount

__all__ = ["benchmark_lookup", "classify_real_discount"]
