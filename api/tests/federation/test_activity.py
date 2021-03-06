import pytest
import uuid

from django.db.models import Q
from django.urls import reverse

from funkwhale_api.federation import (
    activity,
    models,
    api_serializers,
    serializers,
    tasks,
)


def test_receive_validates_basic_attributes_and_stores_activity(factories, now, mocker):
    mocked_dispatch = mocker.patch("funkwhale_api.common.utils.on_commit")
    local_to_actor = factories["users.User"]().create_actor()
    local_cc_actor = factories["users.User"]().create_actor()
    remote_actor = factories["federation.Actor"]()
    a = {
        "@context": [],
        "actor": remote_actor.fid,
        "type": "Noop",
        "id": "https://test.activity",
        "to": [local_to_actor.fid, remote_actor.fid],
        "cc": [local_cc_actor.fid, activity.PUBLIC_ADDRESS],
    }

    copy = activity.receive(activity=a, on_behalf_of=remote_actor)

    assert copy.payload == a
    assert copy.creation_date >= now
    assert copy.actor == remote_actor
    assert copy.fid == a["id"]
    assert copy.type == "Noop"
    mocked_dispatch.assert_called_once_with(
        tasks.dispatch_inbox.delay, activity_id=copy.pk
    )

    assert models.InboxItem.objects.count() == 2
    for actor, t in [(local_to_actor, "to"), (local_cc_actor, "cc")]:
        ii = models.InboxItem.objects.get(actor=actor)
        assert ii.type == t
        assert ii.activity == copy
        assert ii.is_read is False


def test_get_actors_from_audience_urls(settings, db):
    settings.FEDERATION_HOSTNAME = "federation.hostname"
    library_uuid1 = uuid.uuid4()
    library_uuid2 = uuid.uuid4()

    urls = [
        "https://wrong.url",
        "https://federation.hostname"
        + reverse("federation:actors-detail", kwargs={"preferred_username": "kevin"}),
        "https://federation.hostname"
        + reverse("federation:actors-detail", kwargs={"preferred_username": "alice"}),
        "https://federation.hostname"
        + reverse("federation:actors-detail", kwargs={"preferred_username": "bob"}),
        "https://federation.hostname"
        + reverse("federation:music:libraries-detail", kwargs={"uuid": library_uuid1}),
        "https://federation.hostname"
        + reverse("federation:music:libraries-detail", kwargs={"uuid": library_uuid2}),
        activity.PUBLIC_ADDRESS,
    ]
    followed_query = Q(target__followers_url=urls[0])
    for url in urls[1:-1]:
        followed_query |= Q(target__followers_url=url)
    actor_follows = models.Follow.objects.filter(followed_query, approved=True)
    library_follows = models.LibraryFollow.objects.filter(followed_query, approved=True)
    expected = models.Actor.objects.filter(
        Q(fid__in=urls[0:-1])
        | Q(pk__in=actor_follows.values_list("actor", flat=True))
        | Q(pk__in=library_follows.values_list("actor", flat=True))
    )
    assert str(activity.get_actors_from_audience(urls).query) == str(expected.query)


def test_get_inbox_urls(factories):
    a1 = factories["federation.Actor"](
        shared_inbox_url=None, inbox_url="https://a1.inbox"
    )
    a2 = factories["federation.Actor"](
        shared_inbox_url="https://shared.inbox", inbox_url="https://a2.inbox"
    )
    factories["federation.Actor"](
        shared_inbox_url="https://shared.inbox", inbox_url="https://a3.inbox"
    )

    expected = sorted(set([a1.inbox_url, a2.shared_inbox_url]))

    assert activity.get_inbox_urls(a1.__class__.objects.all()) == expected


def test_receive_invalid_data(factories):
    remote_actor = factories["federation.Actor"]()
    a = {"@context": [], "actor": remote_actor.fid, "id": "https://test.activity"}

    with pytest.raises(serializers.serializers.ValidationError):
        activity.receive(activity=a, on_behalf_of=remote_actor)


def test_receive_actor_mismatch(factories):
    remote_actor = factories["federation.Actor"]()
    a = {
        "@context": [],
        "type": "Noop",
        "actor": "https://hello",
        "id": "https://test.activity",
    }

    with pytest.raises(serializers.serializers.ValidationError):
        activity.receive(activity=a, on_behalf_of=remote_actor)


def test_inbox_routing(factories, mocker):
    object = factories["music.Artist"]()
    target = factories["music.Artist"]()
    router = activity.InboxRouter()
    a = factories["federation.Activity"](type="Follow")

    handler_payload = {}
    handler_context = {}

    def handler(payload, context):
        handler_payload.update(payload)
        handler_context.update(context)
        return {"target": target, "object": object}

    router.connect({"type": "Follow"}, handler)

    good_message = {"type": "Follow"}
    router.dispatch(good_message, context={"activity": a})

    assert handler_payload == good_message
    assert handler_context == {"activity": a}

    a.refresh_from_db()

    assert a.object == object
    assert a.target == target


def test_inbox_routing_send_to_channel(factories, mocker):
    group_send = mocker.patch("funkwhale_api.common.channels.group_send")
    a = factories["federation.Activity"](type="Follow")
    ii = factories["federation.InboxItem"](actor__local=True)

    router = activity.InboxRouter()
    handler = mocker.stub()
    router.connect({"type": "Follow"}, handler)
    good_message = {"type": "Follow"}
    router.dispatch(
        good_message, context={"activity": a, "inbox_items": ii.__class__.objects.all()}
    )

    ii.refresh_from_db()

    group_send.assert_called_once_with(
        "user.{}.inbox".format(ii.actor.user.pk),
        {
            "type": "event.send",
            "text": "",
            "data": {
                "type": "inbox.item_added",
                "item": api_serializers.InboxItemSerializer(ii).data,
            },
        },
    )


@pytest.mark.parametrize(
    "route,payload,expected",
    [
        ({"type": "Follow"}, {"type": "Follow"}, True),
        ({"type": "Follow"}, {"type": "Noop"}, False),
        ({"type": "Follow"}, {"type": "Follow", "id": "https://hello"}, True),
        (
            {"type": "Create", "object.type": "Audio"},
            {"type": "Create", "object": {"type": "Note"}},
            False,
        ),
        (
            {"type": "Create", "object.type": "Audio"},
            {"type": "Create", "object": {"type": "Audio"}},
            True,
        ),
    ],
)
def test_route_matching(route, payload, expected):
    assert activity.match_route(route, payload) is expected


def test_outbox_router_dispatch(mocker, factories, now):
    router = activity.OutboxRouter()
    actor = factories["federation.Actor"]()
    r1 = factories["federation.Actor"]()
    r2 = factories["federation.Actor"]()
    mocked_dispatch = mocker.patch("funkwhale_api.common.utils.on_commit")

    def handler(context):
        yield {
            "payload": {
                "type": "Noop",
                "actor": actor.fid,
                "summary": context["summary"],
                "to": [r1],
                "cc": [r2, activity.PUBLIC_ADDRESS],
            },
            "actor": actor,
        }

    expected_deliveries_url = activity.get_inbox_urls(
        models.Actor.objects.filter(pk__in=[r1.pk, r2.pk])
    )
    router.connect({"type": "Noop"}, handler)
    activities = router.dispatch({"type": "Noop"}, {"summary": "hello"})
    a = activities[0]

    mocked_dispatch.assert_called_once_with(
        tasks.dispatch_outbox.delay, activity_id=a.pk
    )

    assert a.payload == {
        "type": "Noop",
        "actor": actor.fid,
        "summary": "hello",
        "to": [r1.fid],
        "cc": [r2.fid, activity.PUBLIC_ADDRESS],
    }
    assert a.actor == actor
    assert a.creation_date >= now
    assert a.uuid is not None

    assert a.deliveries.count() == 2
    for url in expected_deliveries_url:
        delivery = a.deliveries.get(inbox_url=url)
        assert delivery.is_delivered is False


def test_prepare_deliveries_and_inbox_items(factories):
    local_actor1 = factories["federation.Actor"](
        local=True, shared_inbox_url="https://testlocal.inbox"
    )
    local_actor2 = factories["federation.Actor"](
        local=True, shared_inbox_url=local_actor1.shared_inbox_url
    )
    local_actor3 = factories["federation.Actor"](local=True, shared_inbox_url=None)

    remote_actor1 = factories["federation.Actor"](
        shared_inbox_url="https://testremote.inbox"
    )
    remote_actor2 = factories["federation.Actor"](
        shared_inbox_url=remote_actor1.shared_inbox_url
    )
    remote_actor3 = factories["federation.Actor"](shared_inbox_url=None)

    library = factories["music.Library"]()
    library_follower_local = factories["federation.LibraryFollow"](
        target=library, actor__local=True, approved=True
    ).actor
    library_follower_remote = factories["federation.LibraryFollow"](
        target=library, actor__local=False, approved=True
    ).actor
    # follow not approved
    factories["federation.LibraryFollow"](
        target=library, actor__local=False, approved=False
    )

    followed_actor = factories["federation.Actor"]()
    actor_follower_local = factories["federation.Follow"](
        target=followed_actor, actor__local=True, approved=True
    ).actor
    actor_follower_remote = factories["federation.Follow"](
        target=followed_actor, actor__local=False, approved=True
    ).actor
    # follow not approved
    factories["federation.Follow"](
        target=followed_actor, actor__local=False, approved=False
    )

    recipients = [
        local_actor1,
        local_actor2,
        local_actor3,
        remote_actor1,
        remote_actor2,
        remote_actor3,
        activity.PUBLIC_ADDRESS,
        {"type": "followers", "target": library},
        {"type": "followers", "target": followed_actor},
    ]

    inbox_items, deliveries, urls = activity.prepare_deliveries_and_inbox_items(
        recipients, "to"
    )
    expected_inbox_items = sorted(
        [
            models.InboxItem(actor=local_actor1, type="to"),
            models.InboxItem(actor=local_actor2, type="to"),
            models.InboxItem(actor=local_actor3, type="to"),
            models.InboxItem(actor=library_follower_local, type="to"),
            models.InboxItem(actor=actor_follower_local, type="to"),
        ],
        key=lambda v: v.actor.pk,
    )

    expected_deliveries = sorted(
        [
            models.Delivery(inbox_url=remote_actor1.shared_inbox_url),
            models.Delivery(inbox_url=remote_actor3.inbox_url),
            models.Delivery(inbox_url=library_follower_remote.inbox_url),
            models.Delivery(inbox_url=actor_follower_remote.inbox_url),
        ],
        key=lambda v: v.inbox_url,
    )

    expected_urls = [
        local_actor1.fid,
        local_actor2.fid,
        local_actor3.fid,
        remote_actor1.fid,
        remote_actor2.fid,
        remote_actor3.fid,
        activity.PUBLIC_ADDRESS,
        library.followers_url,
        followed_actor.followers_url,
    ]

    assert urls == expected_urls
    assert len(expected_inbox_items) == len(inbox_items)
    assert len(expected_deliveries) == len(deliveries)

    for delivery, expected_delivery in zip(
        sorted(deliveries, key=lambda v: v.inbox_url), expected_deliveries
    ):
        assert delivery.inbox_url == expected_delivery.inbox_url

    for inbox_item, expected_inbox_item in zip(
        sorted(inbox_items, key=lambda v: v.actor.pk), expected_inbox_items
    ):
        assert inbox_item.actor == expected_inbox_item.actor
        assert inbox_item.type == "to"
