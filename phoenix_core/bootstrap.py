from __future__ import annotations

_initialized = False


def init() -> None:
    global _initialized
    if _initialized:
        return
    from . import default_features  # noqa: F401
    from .engines import backtest_engine  # noqa: F401
    from .engines import context_engine  # noqa: F401
    from .engines import cross_market_context_engine  # noqa: F401
    from .engines import decision_engine  # noqa: F401
    from .engines import experiment_engine  # noqa: F401
    from .engines import explain_engine  # noqa: F401
    from .engines import feature_engine  # noqa: F401
    from .engines import feature_importance_engine  # noqa: F401
    from .engines import pattern_engine  # noqa: F401
    from .engines import regime_engine  # noqa: F401
    from .engines import sector_rotation_engine  # noqa: F401
    from .engines import correlation_engine  # noqa: F401
    from .engines import ranking_engine  # noqa: F401
    from .engines import similarity_engine  # noqa: F401
    _initialized = True
