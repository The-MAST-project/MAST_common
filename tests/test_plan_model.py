"""The plan model after control acquisition's step 2 (plans/control_acquisition_design.md 3, 5.10, 8)."""

from __future__ import annotations

import os
import platform
from datetime import UTC, datetime

import pytest
import tomlkit
from pydantic import ValidationError

import common.object_resolver as resolver_module
from common.config.site import Building, Site
from common.models.plans import Plan, PlanProducts, PlanStage, plan_to_toml
from common.models.spectrographs import SpectrographModel
from common.models.targets import Target, resolve_target
from common.object_resolver import ObjectNameError, ResolvedObject
from common.parsers import expand_units

TARGET = {"ra_hours": "12:30:45", "dec_degrees": "+20:15:00"}


def plan(**fields) -> Plan:
    return Plan(**{"target": TARGET, "requested_units": "any", **fields})


class TestRequestedUnits:
    def test_empty_is_refused(self):
        with pytest.raises(ValidationError):
            plan(requested_units=[])

    def test_missing_is_refused(self):
        with pytest.raises(ValidationError):
            Plan(target=TARGET)

    def test_any_is_a_list_to_code_and_a_word_in_the_file(self):
        p = plan(requested_units="any")
        assert p.requested_units == ["any"]
        assert p.model_dump(mode="json")["requested_units"] == "any"
        assert p.requested_unit_names() is None

    def test_one_unit_loads_as_a_list_and_stays_one(self):
        p = plan(requested_units="mast01")
        assert p.requested_units == ["mast01"]
        assert p.model_dump(mode="json")["requested_units"] == ["mast01"]

    def test_any_only_alone(self):
        with pytest.raises(ValidationError, match="sole entry"):
            plan(requested_units=["any", "mast01"])

    def test_not_a_specifier(self):
        with pytest.raises(ValidationError, match="not unit specifiers"):
            plan(requested_units=["mast 01!"])


class TestQuorum:
    def test_zero_is_refused(self):
        with pytest.raises(ValidationError):
            plan(quorum=0)

    def test_above_the_named_units_is_refused(self):
        with pytest.raises(ValidationError, match="quorum 3"):
            plan(requested_units=["mast01", "mast02"], quorum=3)

    def test_any_takes_any_quorum_until_it_is_resolved(self):
        assert plan(requested_units="any", quorum=5).quorum == 5

    def test_a_range_is_counted_once_expanded(self):
        assert plan(requested_units=["ns:1-5"], quorum=3).quorum == 3


class TestFieldsThatWent:
    @pytest.mark.parametrize(
        "name", ["required_units", "allocated_units", "run_folder", "unit_apis", "spec_api", "terminated"]
    )
    def test_not_on_plan(self, name):
        assert name not in Plan.model_fields

    @pytest.mark.parametrize(
        "name", ["requested_exposure_duration", "requested_number_of_exposures", "max_exposure_duration", "repeats"]
    )
    def test_the_exposure_series_is_the_spectrographs(self, name):
        assert name not in Target.model_fields
        assert name in SpectrographModel.model_fields

    def test_an_instrument_needs_a_duration(self):
        with pytest.raises(ValidationError, match="requested_exposure_duration"):
            SpectrographModel(instrument="highspec")
        assert SpectrographModel(instrument="highspec", requested_exposure_duration=60).requested_number_of_exposures == 1
        assert SpectrographModel(instrument=None).requested_exposure_duration is None


class TestPlanToToml:
    def test_scheduled_units_never_allocated(self):
        doc = tomlkit.loads(plan_to_toml(plan()))
        assert doc["scheduled_units"] == []
        assert "allocated_units" not in doc

    def test_no_empty_tables_no_nones_and_merit_kept(self):
        doc = tomlkit.loads(plan_to_toml(plan(ulid="01JAD8Q4ZK7V3M2XG9T6R5N0HB")))
        assert "science" not in doc["target"]
        assert "spec" not in doc and "constraints" not in doc
        assert doc["merit"] == 1
        assert doc["requested_units"] == "any"

    def test_it_loads_back(self):
        p = plan(requested_units=["mast01", "mast03"], quorum=2, autofocus=True)
        again = Plan(**tomlkit.loads(plan_to_toml(p)).unwrap())
        assert again.requested_units == ["mast01", "mast03"] and again.quorum == 2 and again.autofocus

    def test_where_it_was_loaded_from_is_not_written(self):
        p = plan()
        p.full_path = "/somewhere/PLAN_x.toml"  # type: ignore[assignment]
        assert "full_path" not in tomlkit.loads(plan_to_toml(p))


class TestTargets:
    @pytest.fixture
    def resolver(self, monkeypatch):
        m101 = ResolvedObject(name="M101", ra_j2000_hours=14.0535, dec_j2000_degs=54.34875, resolver="sesame",
                              canonical_name="M 101", database="simbad@cds")  # fmt: skip

        def resolve(name, total_timeout):
            if name == "M101":
                return m101
            raise ObjectNameError(f"'{name}' did not resolve")

        monkeypatch.setattr(resolver_module, "resolve_object_name", resolve)

    def test_a_name_without_coordinates_fails_on_load(self):
        with pytest.raises(ValidationError):
            Target(name="M101")  # type: ignore[call-arg]

    def test_coordinates_win_and_no_event(self, resolver):
        target, event = resolve_target({"name": "M101", **TARGET})
        assert (target["ra_hours"], target["dec_degrees"]) == (pytest.approx(12.5125), pytest.approx(20.25))
        assert event is None

    def test_a_name_is_resolved_once_and_recorded(self, resolver):
        target, event = resolve_target({"name": "M101"})
        assert (target["ra_hours"], target["dec_degrees"]) == (14.0535, 54.34875)
        assert target["name"] == "M101", "the name stays, as the label"
        assert event.what == "resolved"
        assert event.details == ["M101 -> ra=14.053500h dec=+54.348750d via simbad@cds", "canonical name: M 101"]

    def test_unusable_coordinates_are_said_first(self, resolver):
        _, event = resolve_target({"name": "M101", "ra_hours": "25:00:00", "dec_degrees": "+20:15:00"})
        assert event.details[0].startswith("coordinates unusable (ra='25:00:00'")

    def test_a_name_that_does_not_resolve_raises(self, resolver):
        with pytest.raises(ObjectNameError):
            resolve_target({"name": "nothing"})

    def test_neither_raises(self, resolver):
        with pytest.raises(ValueError):
            resolve_target({})


@pytest.mark.skipif(platform.system() == "Windows", reason="control, which makes the links, is Linux")
class TestPlanProducts:
    def test_the_folder(self, tmp_path):
        products = PlanProducts.start("01JAD8", now=datetime(2026, 10, 19, 19, 42, 7, tzinfo=UTC), base=str(tmp_path))
        assert products.root == (tmp_path / "PLAN_01JAD8" / "2026-10-19_19-42-07").as_posix()
        assert os.path.isdir(products.root)

    def test_a_second_start_in_the_same_second(self, tmp_path):
        when = datetime(2026, 10, 19, 19, 42, 7, tzinfo=UTC)
        PlanProducts.start("01JAD8", now=when, base=str(tmp_path))
        assert PlanProducts.start("01JAD8", now=when, base=str(tmp_path)).execution == "2026-10-19_19-42-07_2"

    def test_a_relative_link_named_after_its_target(self, tmp_path):
        share = tmp_path / "share"
        target = share / "mast01" / "2026-10-19" / "Acquisitions" / "seq=0007"
        target.mkdir(parents=True)
        products = PlanProducts.start("01JAD8", base=str(share / "plans" / "products"))

        link = products.link(PlanStage.Acquisition, "mast01", target)

        assert link == products.folder_for(PlanStage.Acquisition, "mast01") / "seq=0007"
        assert not os.path.isabs(os.readlink(link))
        assert link.resolve() == target.resolve()

    def test_a_target_not_there_yet_still_gets_its_link_and_twice_is_once(self, tmp_path):
        products = PlanProducts.start("01JAD8", base=str(tmp_path / "products"))
        later = tmp_path / "mast01" / "FocusOnStar" / "0003"
        link = products.link(PlanStage.Autofocus, "mast01", later)
        assert link.is_symlink() and not link.exists()
        assert products.link(PlanStage.Autofocus, "mast01", later) == link


class TestExpandUnits:
    @pytest.fixture
    def ns(self):
        return Site(
            name="ns", project="mast", controller_host="c", spec_host="s", unit_ids="1-20",
            buildings=[Building(names=["south"], unit_ids="1-8"), Building(names=["north"], unit_ids="9-17")],
        )  # fmt: skip

    @pytest.mark.parametrize(
        ("specifiers", "names"),
        [
            (["mast01"], ["mast01"]),
            (["1"], ["mast01"]),
            (["ns:1,3,5"], ["mast01", "mast03", "mast05"]),
            (["ns:10-12"], ["mast10", "mast11", "mast12"]),
            (["ns:north:2"], ["mast10"]),
            (["mast01", "1"], ["mast01"]),
        ],
    )
    def test_to_hostnames(self, ns, specifiers, names):
        assert expand_units(specifiers, [ns], ns) == names

    @pytest.mark.parametrize("bad", ["wis:1", "ns:east:1", "ns:north:10", "mast99", "any"])
    def test_what_names_no_unit_raises(self, ns, bad):
        with pytest.raises(ValueError):
            expand_units([bad], [ns], ns)

    def test_a_plan_expands_through_it(self, ns):
        assert plan(requested_units=["ns:1,3"]).requested_unit_names([ns], ns) == ["mast01", "mast03"]
