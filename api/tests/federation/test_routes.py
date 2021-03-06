import pytest

from funkwhale_api.federation import routes, serializers


@pytest.mark.parametrize(
    "route,handler",
    [
        ({"type": "Follow"}, routes.inbox_follow),
        ({"type": "Accept"}, routes.inbox_accept),
        ({"type": "Create", "object.type": "Audio"}, routes.inbox_create_audio),
        ({"type": "Delete", "object.type": "Library"}, routes.inbox_delete_library),
        ({"type": "Delete", "object.type": "Audio"}, routes.inbox_delete_audio),
        ({"type": "Undo", "object.type": "Follow"}, routes.inbox_undo_follow),
    ],
)
def test_inbox_routes(route, handler):
    for r, h in routes.inbox.routes:
        if r == route:
            assert h == handler
            return

    assert False, "Inbox route {} not found".format(route)


@pytest.mark.parametrize(
    "route,handler",
    [
        ({"type": "Accept"}, routes.outbox_accept),
        ({"type": "Follow"}, routes.outbox_follow),
        ({"type": "Create", "object.type": "Audio"}, routes.outbox_create_audio),
        ({"type": "Delete", "object.type": "Library"}, routes.outbox_delete_library),
        ({"type": "Delete", "object.type": "Audio"}, routes.outbox_delete_audio),
        ({"type": "Undo", "object.type": "Follow"}, routes.outbox_undo_follow),
    ],
)
def test_outbox_routes(route, handler):
    for r, h in routes.outbox.routes:
        if r == route:
            assert h == handler
            return

    assert False, "Outbox route {} not found".format(route)


def test_inbox_follow_library_autoapprove(factories, mocker):
    mocked_outbox_dispatch = mocker.patch(
        "funkwhale_api.federation.activity.OutboxRouter.dispatch"
    )

    local_actor = factories["users.User"]().create_actor()
    remote_actor = factories["federation.Actor"]()
    library = factories["music.Library"](actor=local_actor, privacy_level="everyone")
    ii = factories["federation.InboxItem"](actor=local_actor)

    payload = {
        "type": "Follow",
        "id": "https://test.follow",
        "actor": remote_actor.fid,
        "object": library.fid,
    }

    result = routes.inbox_follow(
        payload,
        context={"actor": remote_actor, "inbox_items": [ii], "raise_exception": True},
    )
    follow = library.received_follows.latest("id")

    assert result["object"] == library
    assert result["related_object"] == follow

    assert follow.fid == payload["id"]
    assert follow.actor == remote_actor
    assert follow.approved is True

    mocked_outbox_dispatch.assert_called_once_with(
        {"type": "Accept"}, context={"follow": follow}
    )


def test_inbox_follow_library_manual_approve(factories, mocker):
    mocked_outbox_dispatch = mocker.patch(
        "funkwhale_api.federation.activity.OutboxRouter.dispatch"
    )

    local_actor = factories["users.User"]().create_actor()
    remote_actor = factories["federation.Actor"]()
    library = factories["music.Library"](actor=local_actor, privacy_level="me")
    ii = factories["federation.InboxItem"](actor=local_actor)

    payload = {
        "type": "Follow",
        "id": "https://test.follow",
        "actor": remote_actor.fid,
        "object": library.fid,
    }

    result = routes.inbox_follow(
        payload,
        context={"actor": remote_actor, "inbox_items": [ii], "raise_exception": True},
    )
    follow = library.received_follows.latest("id")

    assert result["object"] == library
    assert result["related_object"] == follow

    assert follow.fid == payload["id"]
    assert follow.actor == remote_actor
    assert follow.approved is None

    mocked_outbox_dispatch.assert_not_called()


def test_outbox_accept(factories, mocker):
    remote_actor = factories["federation.Actor"]()
    follow = factories["federation.LibraryFollow"](actor=remote_actor)

    activity = list(routes.outbox_accept({"follow": follow}))[0]

    serializer = serializers.AcceptFollowSerializer(
        follow, context={"actor": follow.target.actor}
    )
    expected = serializer.data
    expected["to"] = [follow.actor]

    assert activity["payload"] == expected
    assert activity["actor"] == follow.target.actor
    assert activity["object"] == follow


def test_inbox_accept(factories, mocker):
    mocked_scan = mocker.patch("funkwhale_api.music.models.Library.schedule_scan")
    local_actor = factories["users.User"]().create_actor()
    remote_actor = factories["federation.Actor"]()
    follow = factories["federation.LibraryFollow"](
        actor=local_actor, target__actor=remote_actor
    )
    assert follow.approved is None
    serializer = serializers.AcceptFollowSerializer(
        follow, context={"actor": remote_actor}
    )
    ii = factories["federation.InboxItem"](actor=local_actor)
    result = routes.inbox_accept(
        serializer.data,
        context={"actor": remote_actor, "inbox_items": [ii], "raise_exception": True},
    )
    assert result["object"] == follow
    assert result["related_object"] == follow.target

    follow.refresh_from_db()

    assert follow.approved is True
    mocked_scan.assert_called_once_with(actor=follow.actor)


def test_outbox_follow_library(factories, mocker):
    follow = factories["federation.LibraryFollow"]()
    activity = list(routes.outbox_follow({"follow": follow}))[0]
    serializer = serializers.FollowSerializer(follow, context={"actor": follow.actor})
    expected = serializer.data
    expected["to"] = [follow.target.actor]

    assert activity["payload"] == expected
    assert activity["actor"] == follow.actor
    assert activity["object"] == follow.target


def test_outbox_create_audio(factories, mocker):
    upload = factories["music.Upload"]()
    activity = list(routes.outbox_create_audio({"upload": upload}))[0]
    serializer = serializers.ActivitySerializer(
        {
            "type": "Create",
            "object": serializers.UploadSerializer(upload).data,
            "actor": upload.library.actor.fid,
        }
    )
    expected = serializer.data
    expected["to"] = [{"type": "followers", "target": upload.library}]

    assert dict(activity["payload"]) == dict(expected)
    assert activity["actor"] == upload.library.actor
    assert activity["target"] == upload.library
    assert activity["object"] == upload


def test_inbox_create_audio(factories, mocker):
    activity = factories["federation.Activity"]()
    upload = factories["music.Upload"](bitrate=42, duration=55)
    payload = {
        "type": "Create",
        "actor": upload.library.actor.fid,
        "object": serializers.UploadSerializer(upload).data,
    }
    library = upload.library
    upload.delete()
    init = mocker.spy(serializers.UploadSerializer, "__init__")
    save = mocker.spy(serializers.UploadSerializer, "save")
    assert library.uploads.count() == 0
    result = routes.inbox_create_audio(
        payload,
        context={"actor": library.actor, "raise_exception": True, "activity": activity},
    )
    assert library.uploads.count() == 1
    assert result == {"object": library.uploads.latest("id"), "target": library}

    assert init.call_count == 1
    args = init.call_args
    assert args[1]["data"] == payload["object"]
    assert args[1]["context"] == {"activity": activity, "actor": library.actor}
    assert save.call_count == 1


def test_inbox_delete_library(factories):
    activity = factories["federation.Activity"]()

    library = factories["music.Library"]()
    payload = {
        "type": "Delete",
        "actor": library.actor.fid,
        "object": {"type": "Library", "id": library.fid},
    }

    routes.inbox_delete_library(
        payload,
        context={"actor": library.actor, "raise_exception": True, "activity": activity},
    )

    with pytest.raises(library.__class__.DoesNotExist):
        library.refresh_from_db()


def test_inbox_delete_library_impostor(factories):
    activity = factories["federation.Activity"]()
    impostor = factories["federation.Actor"]()
    library = factories["music.Library"]()
    payload = {
        "type": "Delete",
        "actor": library.actor.fid,
        "object": {"type": "Library", "id": library.fid},
    }

    routes.inbox_delete_library(
        payload,
        context={"actor": impostor, "raise_exception": True, "activity": activity},
    )

    # not deleted, should still be here
    library.refresh_from_db()


def test_outbox_delete_library(factories):
    library = factories["music.Library"]()
    activity = list(routes.outbox_delete_library({"library": library}))[0]
    expected = serializers.ActivitySerializer(
        {"type": "Delete", "object": {"type": "Library", "id": library.fid}}
    ).data

    expected["to"] = [{"type": "followers", "target": library}]

    assert dict(activity["payload"]) == dict(expected)
    assert activity["actor"] == library.actor


def test_inbox_delete_audio(factories):
    activity = factories["federation.Activity"]()

    upload = factories["music.Upload"]()
    library = upload.library
    payload = {
        "type": "Delete",
        "actor": library.actor.fid,
        "object": {"type": "Audio", "id": [upload.fid]},
    }

    routes.inbox_delete_audio(
        payload,
        context={"actor": library.actor, "raise_exception": True, "activity": activity},
    )

    with pytest.raises(upload.__class__.DoesNotExist):
        upload.refresh_from_db()


def test_inbox_delete_audio_impostor(factories):
    activity = factories["federation.Activity"]()
    impostor = factories["federation.Actor"]()
    upload = factories["music.Upload"]()
    library = upload.library
    payload = {
        "type": "Delete",
        "actor": library.actor.fid,
        "object": {"type": "Audio", "id": [upload.fid]},
    }

    routes.inbox_delete_audio(
        payload,
        context={"actor": impostor, "raise_exception": True, "activity": activity},
    )

    # not deleted, should still be here
    upload.refresh_from_db()


def test_outbox_delete_audio(factories):
    upload = factories["music.Upload"]()
    activity = list(routes.outbox_delete_audio({"uploads": [upload]}))[0]
    expected = serializers.ActivitySerializer(
        {"type": "Delete", "object": {"type": "Audio", "id": [upload.fid]}}
    ).data

    expected["to"] = [{"type": "followers", "target": upload.library}]

    assert dict(activity["payload"]) == dict(expected)
    assert activity["actor"] == upload.library.actor


def test_inbox_delete_follow_library(factories):
    local_actor = factories["users.User"]().create_actor()
    remote_actor = factories["federation.Actor"]()
    follow = factories["federation.LibraryFollow"](
        actor=local_actor, target__actor=remote_actor, approved=True
    )
    assert follow.approved is True
    serializer = serializers.UndoFollowSerializer(
        follow, context={"actor": local_actor}
    )
    ii = factories["federation.InboxItem"](actor=local_actor)
    routes.inbox_undo_follow(
        serializer.data,
        context={"actor": local_actor, "inbox_items": [ii], "raise_exception": True},
    )
    with pytest.raises(follow.__class__.DoesNotExist):
        follow.refresh_from_db()


def test_outbox_delete_follow_library(factories):
    remote_actor = factories["federation.Actor"]()
    local_actor = factories["federation.Actor"](local=True)
    follow = factories["federation.LibraryFollow"](
        actor=local_actor, target__actor=remote_actor
    )

    activity = list(routes.outbox_undo_follow({"follow": follow}))[0]

    serializer = serializers.UndoFollowSerializer(
        follow, context={"actor": follow.actor}
    )
    expected = serializer.data
    expected["to"] = [follow.target.actor]

    assert activity["payload"] == expected
    assert activity["actor"] == follow.actor
    assert activity["object"] == follow
    assert activity["related_object"] == follow.target
