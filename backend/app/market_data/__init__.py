"""Источники рыночных данных: ответ источника → MarketSnapshot и FxRate из app/contracts.py

Мосбиржа (параметры облигаций) и ЦБ РФ (курсы валют); Cbonds остался только для истории
Сами в БД не пишут; подключаются в app/api/deps.py
"""

from app.market_data.cbonds import CbondsMarketData
from app.market_data.cbr import CbrFxRates
from app.market_data.moex import MoexMarketData

__all__ = ["CbondsMarketData", "CbrFxRates", "MoexMarketData"]
