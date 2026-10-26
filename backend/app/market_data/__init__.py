"""Источники рыночных данных (Андрей): ответ источника → MarketSnapshot из app/contracts.py

Сам в БД не пишет; подключается в app/api/deps.py
"""

from app.market_data.cbonds import CbondsMarketData

__all__ = ["CbondsMarketData"]
