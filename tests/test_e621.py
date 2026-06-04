import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
import bong_e621
import persist
import user_data


@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    """Reset all state between tests."""
    bong_e621.tag_registry = {}
    bong_e621._store = persist.PersistStore(tmp_path / "test_subs.json", default={})
    bong_e621._store.load()
    bong_e621.tag_registry = dict(bong_e621._store.data)
    user_data._store = persist.PersistStore(tmp_path / "test_users.json", default={})
    user_data._store.load()
    user_data._user_data = user_data._store.data


class TestIsMetaTag:
    def test_rating_prefix(self):
        assert bong_e621._is_meta_tag("rating:s") is True

    def test_order_prefix(self):
        assert bong_e621._is_meta_tag("order:score") is True

    def test_id_prefix(self):
        assert bong_e621._is_meta_tag("id:12345") is True

    def test_score_prefix(self):
        assert bong_e621._is_meta_tag("score:>10") is True

    def test_negation(self):
        assert bong_e621._is_meta_tag("-tag") is True

    def test_tilde_not_meta(self):
        assert bong_e621._is_meta_tag("~tag") is False

    def test_wildcard(self):
        assert bong_e621._is_meta_tag("prot*") is True

    def test_wildcard_middle(self):
        assert bong_e621._is_meta_tag("pro*gen") is True

    def test_normal_tag(self):
        assert bong_e621._is_meta_tag("protogen") is False

    def test_artist_prefix_not_meta(self):
        assert bong_e621._is_meta_tag("artist:name") is False

    def test_species_prefix_not_meta(self):
        assert bong_e621._is_meta_tag("species:wolf") is False

    def test_favcount_prefix(self):
        assert bong_e621._is_meta_tag("favcount:>5") is True

    def test_date_prefix(self):
        assert bong_e621._is_meta_tag("date:2024-01-01") is True


class TestSplitTagForValidation:
    def test_artist_prefix(self):
        result = bong_e621._split_tag_for_validation("artist:shirokuroneko")
        assert result == [("shirokuroneko", 1)]

    def test_species_prefix(self):
        result = bong_e621._split_tag_for_validation("species:wolf")
        assert result == [("wolf", 5)]

    def test_character_prefix(self):
        result = bong_e621._split_tag_for_validation("character:ridley")
        assert result == [("ridley", 4)]

    def test_copyright_prefix(self):
        result = bong_e621._split_tag_for_validation("copyright:zootopia")
        assert result == [("zootopia", 3)]

    def test_lore_prefix(self):
        result = bong_e621._split_tag_for_validation("lore:something")
        assert result == [("something", 8)]

    def test_meta_prefix(self):
        result = bong_e621._split_tag_for_validation("meta:highres")
        assert result == [("highres", 7)]

    def test_normal_tag(self):
        result = bong_e621._split_tag_for_validation("protogen")
        assert result == [("protogen", None)]

    def test_meta_tag_skipped(self):
        result = bong_e621._split_tag_for_validation("rating:s")
        assert result == []

    def test_negation_skipped(self):
        result = bong_e621._split_tag_for_validation("-wolf")
        assert result == []

    def test_wildcard_skipped(self):
        result = bong_e621._split_tag_for_validation("prot*")
        assert result == []

    def test_or_operator_strips_tilde(self):
        result = bong_e621._split_tag_for_validation("~protogen")
        assert result == [("protogen", None)]

    def test_double_tilde_strips(self):
        result = bong_e621._split_tag_for_validation("~~protogen")
        assert result == [("protogen", None)]


class TestValidateTag:
    def test_tag_exists(self):
        bong_e621._e621_request = lambda url, params: {
            "tags": [{"name": "protogen", "post_count": 5000}]
        }
        exists, count = bong_e621._validate_tag("protogen")
        assert exists is True
        assert count == 5000

    def test_tag_exists_with_category(self):
        bong_e621._e621_request = lambda url, params: {
            "tags": [{"name": "shirokuroneko", "post_count": 200, "category": 1}]
        }
        exists, count = bong_e621._validate_tag("shirokuroneko", category=1)
        assert exists is True
        assert count == 200

    def test_tag_not_found_dict_empty(self):
        bong_e621._e621_request = lambda url, params: {"tags": {}}
        exists, count = bong_e621._validate_tag("nonexistenttagxyz")
        assert exists is False
        assert count is None

    def test_tag_not_found_list_empty(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        exists, count = bong_e621._validate_tag("nonexistenttagxyz")
        assert exists is False
        assert count is None

    def test_api_failure_returns_true(self):
        bong_e621._e621_request = lambda url, params: None
        exists, count = bong_e621._validate_tag("protogen")
        assert exists is True
        assert count is None

    def test_exists_but_zero_posts(self):
        bong_e621._e621_request = lambda url, params: {
            "tags": [{"name": "newtag", "post_count": 0}]
        }
        exists, count = bong_e621._validate_tag("newtag")
        assert exists is True
        assert count == 0


class TestAddSubscription:
    def test_add_basic(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        result = bong_e621.add_subscription(123, "protogen")
        assert "subscribed" in result.lower()
        assert user_data.get_e621_subs(123) == ["protogen"]
        assert "protogen" in bong_e621.tag_registry
        assert bong_e621.tag_registry["protogen"] is None

    def test_add_normalizes_tags(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "  Protogen  ")
        assert user_data.get_e621_subs(123) == ["protogen"]

    def test_add_duplicate(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "protogen")
        result = bong_e621.add_subscription(123, "protogen")
        assert "already subscribed" in result.lower()
        assert user_data.get_e621_subs(123) == ["protogen"]

    def test_add_different_users_same_tags(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "protogen")
        assert user_data.get_e621_subs(123) == ["protogen"]
        assert user_data.get_e621_subs(456) == ["protogen"]
        assert len(bong_e621.tag_registry) == 1

    def test_add_empty_tags(self):
        result = bong_e621.add_subscription(123, "  ")
        assert "empty" in result.lower()
        assert user_data.get_e621_subs(123) == []

    def test_add_with_warning_unknown_tag(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        result = bong_e621.add_subscription(123, "nonexistenttagxyz")
        assert "not found" in result.lower()

    def test_add_with_warning_zero_posts(self):
        bong_e621._e621_request = lambda url, params: {
            "tags": [{"name": "newtag", "post_count": 0}]
        }
        result = bong_e621.add_subscription(123, "newtag")
        assert "no posts yet" in result.lower()

    def test_add_with_rating_meta_tag_skips_validation(self):
        bong_e621.add_subscription(123, "protogen rating:s")
        assert user_data.get_e621_subs(123) == ["protogen rating:s"]

    def test_add_with_artist_prefix(self):
        call_count = 0
        def mock_request(url, params):
            nonlocal call_count
            call_count += 1
            if "tags.json" in url:
                name = params.get("search[name_matches]", "")
                cat = params.get("search[category]")
                if name == "shirokuroneko" and cat == 1:
                    return {"tags": [{"name": "shirokuroneko", "post_count": 200}]}
                return {"tags": []}
            return {"posts": []}
        bong_e621._e621_request = mock_request
        result = bong_e621.add_subscription(123, "artist:shirokuroneko")
        assert "subscribed" in result.lower()


class TestRemoveSubscription:
    def test_remove_existing(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.tag_registry["protogen"] = 50000
        result = bong_e621.remove_subscription(123, "protogen")
        assert "unsubscribed" in result.lower()
        assert user_data.get_e621_subs(123) == []
        assert "protogen" not in bong_e621.tag_registry

    def test_remove_nonexistent(self):
        result = bong_e621.remove_subscription(123, "protogen")
        assert "no subscription" in result.lower()

    def test_remove_keeps_tag_if_other_subscribers(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "protogen")
        bong_e621.remove_subscription(123, "protogen")
        assert "protogen" in bong_e621.tag_registry
        assert user_data.get_e621_subs(123) == []
        assert user_data.get_e621_subs(456) == ["protogen"]

    def test_remove_only_affects_user(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "protogen")
        bong_e621.remove_subscription(123, "protogen")
        assert user_data.get_e621_subs(456) == ["protogen"]


class TestListSubscriptions:
    def test_list_empty(self):
        result = bong_e621.list_subscriptions(123)
        assert "no" in result.lower()

    def test_list_with_subs(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(123, "wolf")
        result = bong_e621.list_subscriptions(123)
        assert "protogen" in result
        assert "wolf" in result

    def test_list_only_shows_own(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "wolf")
        result = bong_e621.list_subscriptions(123)
        assert "protogen" in result
        assert "wolf" not in result


class TestGetAllSubscribers:
    def test_finds_subscribers(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "protogen")
        subs = user_data.get_all_e621_subscribers("protogen")
        assert 123 in subs
        assert 456 in subs

    def test_no_subscribers(self):
        subs = user_data.get_all_e621_subscribers("nonexistent")
        assert subs == []

    def test_different_tags(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "wolf")
        assert user_data.get_all_e621_subscribers("protogen") == [123]
        assert user_data.get_all_e621_subscribers("wolf") == [456]


class TestGetNewPosts:
    def test_new_posts_filter_by_id(self):
        bong_e621._e621_request = lambda url, params: {
            "posts": [
                {"id": 100, "score": {"total": 10}, "rating": "s"},
                {"id": 101, "score": {"total": 5}, "rating": "s"},
                {"id": 102, "score": {"total": 20}, "rating": "s"},
            ],
            "post_count": 3,
        }
        posts, new_id = bong_e621.get_new_posts("protogen", 100)
        assert len(posts) == 2
        assert posts[0]["id"] == 101
        assert posts[1]["id"] == 102
        assert new_id == 102

    def test_first_poll_sets_id(self):
        bong_e621._e621_request = lambda url, params: {
            "posts": [
                {"id": 50, "score": {"total": 10}, "rating": "s"},
                {"id": 55, "score": {"total": 5}, "rating": "s"},
            ],
            "post_count": 2,
        }
        posts, new_id = bong_e621.get_new_posts("protogen", None)
        assert posts == []
        assert new_id == 55

    def test_no_new_posts(self):
        bong_e621._e621_request = lambda url, params: {
            "posts": [
                {"id": 100, "score": {"total": 10}, "rating": "s"},
            ],
            "post_count": 1,
        }
        posts, new_id = bong_e621.get_new_posts("protogen", 200)
        assert posts == []
        assert new_id == 200

    def test_api_failure_returns_same_id(self):
        bong_e621._e621_request = lambda url, params: None
        posts, new_id = bong_e621.get_new_posts("protogen", 100)
        assert posts == []
        assert new_id == 100

    def test_empty_response(self):
        bong_e621._e621_request = lambda url, params: {"posts": [], "post_count": 0}
        posts, new_id = bong_e621.get_new_posts("protogen", 100)
        assert posts == []
        assert new_id == 100

    def test_first_poll_empty_response(self):
        bong_e621._e621_request = lambda url, params: {"posts": [], "post_count": 0}
        posts, new_id = bong_e621.get_new_posts("protogen", None)
        assert posts == []
        assert new_id is None

    def test_uses_posts_endpoint(self):
        calls = []
        def mock_request(url, params):
            calls.append(url)
            return {"posts": [{"id": 10, "score": {"total": 1}, "rating": "s"}], "post_count": 1}
        bong_e621._e621_request = mock_request
        bong_e621.get_new_posts("test", None)
        assert any("posts.json" in c for c in calls)


class TestSearchFormatting:
    def test_search_formats_results(self):
        bong_e621._e621_request = lambda url, params: {
            "posts": [
                {"id": 123, "score": {"total": 42}, "rating": "s", "file": {"ext": "png"}},
                {"id": 456, "score": {"total": 10}, "rating": "e", "file": {"ext": "jpg"}},
            ],
            "post_count": 2,
        }
        result = bong_e621.search_e621_posts("protogen", limit=5)
        assert "#123" in result
        assert "#456" in result
        assert "score:42" in result
        assert "rating:s" in result
        assert "e621.net/posts/123" in result

    def test_search_no_results(self):
        bong_e621._e621_request = lambda url, params: {"posts": [], "post_count": 0}
        result = bong_e621.search_e621_posts("nonexistent_tag_xyz")
        assert "no results" in result.lower() or "No results" in result

    def test_search_api_failure(self):
        bong_e621._e621_request = lambda url, params: None
        result = bong_e621.search_e621_posts("protogen")
        assert "could not reach" in result.lower() or "try again" in result.lower()


class TestCleanupTagRegistry:
    def test_cleanup_removes_orphan_tags(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "wolf")
        bong_e621.remove_subscription(123, "protogen")
        bong_e621.remove_subscription(456, "wolf")
        bong_e621.cleanup_tag_registry()
        assert len(bong_e621.tag_registry) == 0

    def test_cleanup_keeps_active_tags(self):
        bong_e621._e621_request = lambda url, params: {"tags": []}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.cleanup_tag_registry()
        assert "protogen" in bong_e621.tag_registry


class TestTagRegistryGlobalState:
    def test_shared_tag_registry(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.add_subscription(456, "protogen")
        assert len(bong_e621.tag_registry) == 1

    def test_new_user_subscribes_to_existing_tag(self):
        bong_e621._e621_request = lambda url, params: {"tags": [{"name": "protogen", "post_count": 5000}]}
        bong_e621.add_subscription(123, "protogen")
        bong_e621.tag_registry["protogen"] = 50000
        bong_e621.add_subscription(456, "protogen")
        assert bong_e621.tag_registry["protogen"] == 50000


class TestLoadSubscriptionsMigration:
    def test_loads_flat_dict(self):
        import json
        subs_path = bong_e621._store.path
        subs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(subs_path, "w") as f:
            json.dump({"protogen": 50000, "wolf": 40000}, f)
        bong_e621.load_subscriptions()
        assert bong_e621.tag_registry == {"protogen": 50000, "wolf": 40000}

    def test_loads_old_format_migrates(self):
        import json
        subs_path = bong_e621._store.path
        subs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(subs_path, "w") as f:
            json.dump({"tag_registry": {"protogen": 50000}, "subscriptions": [{"user_id": 123, "tags": "protogen"}]}, f)
        bong_e621.load_subscriptions()
        assert bong_e621.tag_registry == {"protogen": 50000}