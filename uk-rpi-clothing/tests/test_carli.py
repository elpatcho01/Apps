import math

import pytest

from rpiclothing import carli
from rpiclothing.carli import Quote


def test_sale_bounce_example_from_the_brief():
    agg = carli.elementary([2.0, 0.5])
    assert agg.carli == pytest.approx(1.25)
    assert agg.jevons == pytest.approx(1.0)


def test_gap_is_about_half_the_log_variance_for_small_dispersion():
    rs = [0.97, 1.0, 1.03, 0.99, 1.02, 0.98]
    agg = carli.elementary(rs)
    assert agg.gap_log == pytest.approx(agg.var_log / 2, rel=0.05)


def test_no_dispersion_no_gap():
    agg = carli.elementary([1.1, 1.1, 1.1])
    assert agg.carli == pytest.approx(agg.jevons) and agg.var_log == pytest.approx(0.0)


def test_rejects_nonpositive_relatives():
    with pytest.raises(ValueError):
        carli.elementary([1.0, 0.0])


def test_shop_weights_change_the_mean_only_when_asked():
    qs = [Quote("i", "s", 2.0, shop_weight=3), Quote("i", "s", 1.0, shop_weight=1)]
    assert carli.item_indices(qs)["i"].carli == pytest.approx(1.5)
    assert carli.item_indices(qs, shop_weighted=True)["i"].carli == pytest.approx(1.75)


def test_strata_combine_with_stratum_weights():
    qs = [Quote("i", "a", 1.2, stratum_weight=3), Quote("i", "b", 1.0, stratum_weight=1)]
    assert carli.item_indices(qs)["i"].carli == pytest.approx((3 * 1.2 + 1.0) / 4)


def test_inconsistent_stratum_weights_raise():
    qs = [Quote("i", "a", 1.2, stratum_weight=3), Quote("i", "a", 1.0, stratum_weight=2)]
    with pytest.raises(ValueError):
        carli.item_indices(qs)


def test_section_excludes_unweighted_items_and_reports_coverage():
    items = carli.item_indices([Quote("a", "s", 1.1), Quote("b", "s", 1.3)])
    w = {"a": 3.0, "c": 1.0}
    assert carli.section_index(items, w, "carli") == pytest.approx(1.1)
    assert carli.weight_coverage(items, w) == pytest.approx(0.75)


def test_bootstrap_sd_shrinks_with_more_quotes():
    import random
    random.seed(0)
    small = [Quote("i", "s", math.exp(random.gauss(0, 0.2))) for _ in range(10)]
    big = [Quote("i", "s", math.exp(random.gauss(0, 0.2))) for _ in range(250)]
    sd_small, _ = carli.bootstrap_section_sd(small, {"i": 1}, reps=200)
    sd_big, _ = carli.bootstrap_section_sd(big, {"i": 1}, reps=200)
    assert sd_big < sd_small / 3
