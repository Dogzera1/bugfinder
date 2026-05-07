"""
Calculadora de viabilidade de revenda no Mercado Livre.

Modelo simplificado:
  receita_liquida = preco_venda_ml * (1 - ml_fee_pct) - taxa_fixa - frete_absorvido
  custo_aquisicao = preco_oferta + frete_compra
  margem_brl     = receita_liquida - custo_aquisicao
  roi_pct        = margem_brl / custo_aquisicao

Defaults conservadores:
  ml_fee_pct = 0.14         (média entre Clássico ~12% e Premium ~17%)
  frete_compra = 0          (varia muito; usuário ajusta)
  frete_venda  = R$ 20      (estimativa de envio absorvido em itens > R$79)

Tributação (MEI/Simples) NÃO contemplada — produz um piso; o real pode ser menor.
"""
from __future__ import annotations

from .models import Viability


DEFAULT_ML_FEE_PCT = 0.14
DEFAULT_FREIGHT_BUY = 0.0
DEFAULT_FREIGHT_SELL = 20.0
DEFAULT_FIXED_FEE_THRESHOLD = 79.0
DEFAULT_FIXED_FEE = 6.0


def compute_viability(
    *,
    offer_price: float,
    ml_sale_price: float,
    ml_fee_pct: float = DEFAULT_ML_FEE_PCT,
    freight_buy: float = DEFAULT_FREIGHT_BUY,
    freight_sell: float = DEFAULT_FREIGHT_SELL,
    apply_fixed_fee: bool = True,
) -> Viability:
    acquisition = float(offer_price) + float(freight_buy)
    fee_brl = ml_sale_price * ml_fee_pct
    fixed_fee = (
        DEFAULT_FIXED_FEE
        if apply_fixed_fee and ml_sale_price < DEFAULT_FIXED_FEE_THRESHOLD
        else 0.0
    )
    net_rev = ml_sale_price - fee_brl - fixed_fee - freight_sell
    margin = net_rev - acquisition
    roi = (margin / acquisition * 100.0) if acquisition > 0 else 0.0
    return Viability(
        acquisition_cost=acquisition,
        ml_sale_price=ml_sale_price,
        ml_fee_pct=ml_fee_pct,
        ml_fee_brl=fee_brl,
        fixed_fee_brl=fixed_fee,
        freight_buy=freight_buy,
        freight_sell=freight_sell,
        net_revenue=net_rev,
        margin_brl=margin,
        roi_pct=roi,
    )
