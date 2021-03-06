import uuid
import logging

from django.db import transaction, IntegrityError
from django.db.models import Q

from funkwhale_api.common import channels
from funkwhale_api.common import utils as funkwhale_utils


logger = logging.getLogger(__name__)
PUBLIC_ADDRESS = "https://www.w3.org/ns/activitystreams#Public"

ACTIVITY_TYPES = [
    "Accept",
    "Add",
    "Announce",
    "Arrive",
    "Block",
    "Create",
    "Delete",
    "Dislike",
    "Flag",
    "Follow",
    "Ignore",
    "Invite",
    "Join",
    "Leave",
    "Like",
    "Listen",
    "Move",
    "Offer",
    "Question",
    "Reject",
    "Read",
    "Remove",
    "TentativeReject",
    "TentativeAccept",
    "Travel",
    "Undo",
    "Update",
    "View",
]


OBJECT_TYPES = [
    "Article",
    "Audio",
    "Collection",
    "Document",
    "Event",
    "Image",
    "Note",
    "OrderedCollection",
    "Page",
    "Place",
    "Profile",
    "Relationship",
    "Tombstone",
    "Video",
] + ACTIVITY_TYPES


BROADCAST_TO_USER_ACTIVITIES = ["Follow", "Accept"]


@transaction.atomic
def receive(activity, on_behalf_of):
    from . import models
    from . import serializers
    from . import tasks

    # we ensure the activity has the bare minimum structure before storing
    # it in our database
    serializer = serializers.BaseActivitySerializer(
        data=activity, context={"actor": on_behalf_of, "local_recipients": True}
    )
    serializer.is_valid(raise_exception=True)
    try:
        copy = serializer.save()
    except IntegrityError:
        logger.warning(
            "[federation] Discarding already elivered activity %s",
            serializer.validated_data.get("id"),
        )
        return

    local_to_recipients = get_actors_from_audience(activity.get("to", []))
    local_to_recipients = local_to_recipients.exclude(user=None)

    local_cc_recipients = get_actors_from_audience(activity.get("cc", []))
    local_cc_recipients = local_cc_recipients.exclude(user=None)

    inbox_items = []
    for recipients, type in [(local_to_recipients, "to"), (local_cc_recipients, "cc")]:

        for r in recipients.values_list("pk", flat=True):
            inbox_items.append(models.InboxItem(actor_id=r, type=type, activity=copy))

    models.InboxItem.objects.bulk_create(inbox_items)

    # at this point, we have the activity in database. Even if we crash, it's
    # okay, as we can retry later
    funkwhale_utils.on_commit(tasks.dispatch_inbox.delay, activity_id=copy.pk)
    return copy


class Router:
    def __init__(self):
        self.routes = []

    def connect(self, route, handler):
        self.routes.append((route, handler))

    def register(self, route):
        def decorator(handler):
            self.connect(route, handler)
            return handler

        return decorator


class InboxRouter(Router):
    @transaction.atomic
    def dispatch(self, payload, context):
        """
        Receives an Activity payload and some context and trigger our
        business logic
        """
        from . import api_serializers
        from . import models

        for route, handler in self.routes:
            if match_route(route, payload):
                r = handler(payload, context=context)
                activity_obj = context.get("activity")
                if activity_obj and r:
                    # handler returned additional data we can use
                    # to update the activity target
                    for key, value in r.items():
                        setattr(activity_obj, key, value)

                    update_fields = []
                    for k in r.keys():
                        if k in ["object", "target", "related_object"]:
                            update_fields += [
                                "{}_id".format(k),
                                "{}_content_type".format(k),
                            ]
                        else:
                            update_fields.append(k)
                    activity_obj.save(update_fields=update_fields)

                if payload["type"] not in BROADCAST_TO_USER_ACTIVITIES:
                    return

                inbox_items = context.get(
                    "inbox_items", models.InboxItem.objects.none()
                )
                inbox_items = (
                    inbox_items.select_related()
                    .select_related("actor__user")
                    .prefetch_related(
                        "activity__object",
                        "activity__target",
                        "activity__related_object",
                    )
                )

                for ii in inbox_items:
                    user = ii.actor.get_user()
                    if not user:
                        continue
                    group = "user.{}.inbox".format(user.pk)
                    channels.group_send(
                        group,
                        {
                            "type": "event.send",
                            "text": "",
                            "data": {
                                "type": "inbox.item_added",
                                "item": api_serializers.InboxItemSerializer(ii).data,
                            },
                        },
                    )
                return


class OutboxRouter(Router):
    @transaction.atomic
    def dispatch(self, routing, context):
        """
        Receives a routing payload and some business objects in the context
        and may yield data that should be persisted in the Activity model
        for further delivery.
        """
        from . import models
        from . import tasks

        for route, handler in self.routes:
            if not match_route(route, routing):
                continue

            activities_data = []
            for e in handler(context):
                # a route can yield zero, one or more activity payloads
                if e:
                    activities_data.append(e)
            inbox_items_by_activity_uuid = {}
            deliveries_by_activity_uuid = {}
            prepared_activities = []
            for activity_data in activities_data:
                activity_data["payload"]["actor"] = activity_data["actor"].fid
                to = activity_data["payload"].pop("to", [])
                cc = activity_data["payload"].pop("cc", [])
                a = models.Activity(**activity_data)
                a.uuid = uuid.uuid4()
                to_inbox_items, to_deliveries, new_to = prepare_deliveries_and_inbox_items(
                    to, "to"
                )
                cc_inbox_items, cc_deliveries, new_cc = prepare_deliveries_and_inbox_items(
                    cc, "cc"
                )
                if not any(
                    [to_inbox_items, to_deliveries, cc_inbox_items, cc_deliveries]
                ):
                    continue
                deliveries_by_activity_uuid[str(a.uuid)] = to_deliveries + cc_deliveries
                inbox_items_by_activity_uuid[str(a.uuid)] = (
                    to_inbox_items + cc_inbox_items
                )
                if new_to:
                    a.payload["to"] = new_to
                if new_cc:
                    a.payload["cc"] = new_cc
                prepared_activities.append(a)

            activities = models.Activity.objects.bulk_create(prepared_activities)

            for activity in activities:
                if str(activity.uuid) in deliveries_by_activity_uuid:
                    for obj in deliveries_by_activity_uuid[str(a.uuid)]:
                        obj.activity = activity

                if str(activity.uuid) in inbox_items_by_activity_uuid:
                    for obj in inbox_items_by_activity_uuid[str(a.uuid)]:
                        obj.activity = activity

            # create all deliveries and items, in bulk
            models.Delivery.objects.bulk_create(
                [
                    obj
                    for collection in deliveries_by_activity_uuid.values()
                    for obj in collection
                ]
            )
            models.InboxItem.objects.bulk_create(
                [
                    obj
                    for collection in inbox_items_by_activity_uuid.values()
                    for obj in collection
                ]
            )

            for a in activities:
                funkwhale_utils.on_commit(tasks.dispatch_outbox.delay, activity_id=a.pk)
            return activities


def recursive_gettattr(obj, key):
    """
    Given a dictionary such as {'user': {'name': 'Bob'}} and
    a dotted string such as user.name, returns 'Bob'.

    If the value is not present, returns None
    """
    v = obj
    for k in key.split("."):
        v = v.get(k)
        if v is None:
            return

    return v


def match_route(route, payload):
    for key, value in route.items():
        payload_value = recursive_gettattr(payload, key)
        if payload_value != value:
            return False

    return True


def prepare_deliveries_and_inbox_items(recipient_list, type):
    """
    Given a list of recipients (
        either actor instances, public adresses, a dictionnary with a "type" and "target"
        keys for followers collections)
    returns a list of deliveries, alist of inbox_items and a list
    of urls to persist in the activity in place of the initial recipient list.
    """
    from . import models

    local_recipients = set()
    remote_inbox_urls = set()
    urls = []

    for r in recipient_list:
        if isinstance(r, models.Actor):
            if r.is_local:
                local_recipients.add(r)
            else:
                remote_inbox_urls.add(r.shared_inbox_url or r.inbox_url)
            urls.append(r.fid)
        elif r == PUBLIC_ADDRESS:
            urls.append(r)
        elif isinstance(r, dict) and r["type"] == "followers":
            received_follows = (
                r["target"]
                .received_follows.filter(approved=True)
                .select_related("actor__user")
            )
            for follow in received_follows:
                actor = follow.actor
                if actor.is_local:
                    local_recipients.add(actor)
                else:
                    remote_inbox_urls.add(actor.shared_inbox_url or actor.inbox_url)
            urls.append(r["target"].followers_url)

    deliveries = [models.Delivery(inbox_url=url) for url in remote_inbox_urls]
    inbox_items = [
        models.InboxItem(actor=actor, type=type) for actor in local_recipients
    ]

    return inbox_items, deliveries, urls


def join_queries_or(left, right):
    if left:
        return left | right
    else:
        return right


def get_actors_from_audience(urls):
    """
    Given a list of urls such as [
        "https://hello.world/@bob/followers",
        "https://eldritch.cafe/@alice/followers",
        "https://funkwhale.demo/libraries/uuid/followers",
    ]
    Returns a queryset of actors that are member of the collections
    listed in the given urls. The urls may contain urls referring
    to an actor, an actor followers collection or an library followers
    collection.

    Urls that don't match anything are simply discarded
    """
    from . import models

    queries = {"followed": None, "actors": []}
    for url in urls:
        if url == PUBLIC_ADDRESS:
            continue
        queries["actors"].append(url)
        queries["followed"] = join_queries_or(
            queries["followed"], Q(target__followers_url=url)
        )
    final_query = None
    if queries["actors"]:
        final_query = join_queries_or(final_query, Q(fid__in=queries["actors"]))
    if queries["followed"]:
        actor_follows = models.Follow.objects.filter(queries["followed"], approved=True)
        final_query = join_queries_or(
            final_query, Q(pk__in=actor_follows.values_list("actor", flat=True))
        )

        library_follows = models.LibraryFollow.objects.filter(
            queries["followed"], approved=True
        )
        final_query = join_queries_or(
            final_query, Q(pk__in=library_follows.values_list("actor", flat=True))
        )
    if not final_query:
        return models.Actor.objects.none()
    return models.Actor.objects.filter(final_query)


def get_inbox_urls(actor_queryset):
    """
    Given an actor queryset, returns a deduplicated set containing
    all inbox or shared inbox urls where we should deliver our payloads for
    those actors
    """
    values = actor_queryset.values("inbox_url", "shared_inbox_url")

    urls = set([actor["shared_inbox_url"] or actor["inbox_url"] for actor in values])
    return sorted(urls)
