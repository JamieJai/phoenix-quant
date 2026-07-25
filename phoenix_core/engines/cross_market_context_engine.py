from ..interfaces import Engine
from ..registry import EngineRegistry
from ..models import CrossMarketContextResult
from ..features.cross_market_features import compute_cross_market_features
@EngineRegistry.register("cross_market_context_engine", "cross_market_v1")
class CrossMarketContextEngine(Engine):
    slot="cross_market_context_engine"; name="cross_market_v1"
    def run(self, input_data): return CrossMarketContextResult(input_data.as_of, compute_cross_market_features(input_data.market_ohlcv or input_data.ohlcv or {}, input_data.as_of))
