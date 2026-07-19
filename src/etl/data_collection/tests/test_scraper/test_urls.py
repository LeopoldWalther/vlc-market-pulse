"""
Unit tests for operation strategies and the URL builder (FEATURE-002, task 1.3).
"""

from __future__ import annotations

import pytest

from data_collection.scraper.urls import (
    IdealistaUrlBuilder,
    OperationStrategy,
    RentStrategy,
    SaleStrategy,
)


class TestOperationStrategies:
    def test_sale_strategy_segment_and_label(self) -> None:
        strategy = SaleStrategy()
        assert strategy.segment == "venta-viviendas"
        assert strategy.operation_label == "sale"

    def test_rent_strategy_segment_and_label(self) -> None:
        strategy = RentStrategy()
        assert strategy.segment == "alquiler-viviendas"
        assert strategy.operation_label == "rent"

    def test_operation_strategy_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            OperationStrategy()  # type: ignore[abstract]

    def test_strategies_are_interchangeable_via_common_interface(self) -> None:
        for strategy in (SaleStrategy(), RentStrategy()):
            assert isinstance(strategy, OperationStrategy)
            assert isinstance(strategy.segment, str)
            assert isinstance(strategy.operation_label, str)


class TestIdealistaUrlBuilder:
    def test_sale_url_for_page(self) -> None:
        builder = IdealistaUrlBuilder(SaleStrategy())
        url = builder.build(page=1)
        assert (
            url
            == "https://www.idealista.com/en/venta-viviendas/valencia-valencia/?pagina=1"
        )

    def test_rent_url_for_page(self) -> None:
        builder = IdealistaUrlBuilder(RentStrategy())
        url = builder.build(page=3)
        assert (
            url
            == "https://www.idealista.com/en/alquiler-viviendas/valencia-valencia/?pagina=3"
        )

    def test_no_filter_segments_present(self) -> None:
        """Full-inventory scrape: no size/elevator/preservation filters."""
        builder = IdealistaUrlBuilder(SaleStrategy())
        url = builder.build(page=1)
        for forbidden in ("minSize", "maxSize", "elevator", "preservation"):
            assert forbidden not in url

    def test_rejects_page_below_one(self) -> None:
        builder = IdealistaUrlBuilder(SaleStrategy())
        with pytest.raises(ValueError):
            builder.build(page=0)

    def test_swapping_strategy_requires_no_builder_change(self) -> None:
        """OCP: the same builder class works for any strategy variant."""
        sale_url = IdealistaUrlBuilder(SaleStrategy()).build(page=1)
        rent_url = IdealistaUrlBuilder(RentStrategy()).build(page=1)
        assert sale_url != rent_url
