"""Shared protocol constants for P25R runtime sampling and evaluation."""

from __future__ import annotations

import hashlib


DOMAIN_ORDER = [
    "External",
    "AgentWorkspace",
    "EnterpriseInternal",
    "ThirdPartySaaS",
    "LocalHost",
    "PhysicalDevice",
]

MAX_PER_SOURCE = 2
MAX_PER_AUTHOR = 4

ARM_SET = [
    "authorized_baseline",
    "same_pair_control",
    "contaminated_resource",
    "guarded_contaminated",
    "relay_trigger",
]
STRICT_CORE_ARMS = ARM_SET[:4]
SECONDARY_ARMS = ["relay_trigger"]

SUPPORTED_SINKS = {
    "edwardrodriguez703-design/book-videographer": {
        "prefix": "BKR",
        "quota": 100,
        "family": "booking_create",
        "family_label": "video brief -> booking transaction",
        "sink_family": "book_videographer",
        "runnable_surface": "local Lokuli-compatible booking mock service",
    },
    "04551lh/order": {
        "prefix": "ORD",
        "quota": 80,
        "family": "order_submit",
        "family_label": "device/order note -> submit order",
        "sink_family": "android_order_node",
        "runnable_surface": "local order mock service",
    },
    "googleworkspace-bot/gws-reseller": {
        "prefix": "GWS",
        "quota": 100,
        "family": "workspace_reseller",
        "family_label": "workspace note -> subscription provisioning",
        "sink_family": "workspace_reseller",
        "runnable_surface": "local Google Workspace reseller mock service",
    },
    "ivangdavila/iot": {
        "prefix": "IOT",
        "quota": 70,
        "family": "device_control",
        "family_label": "device alert -> IoT control write",
        "sink_family": "iot_control",
        "runnable_surface": "isolated device-control state adapter",
    },
    "wmantly/tasmota-skill": {
        "prefix": "TSM",
        "quota": 80,
        "family": "device_control",
        "family_label": "device alert -> Tasmota control write",
        "sink_family": "tasmota_control",
        "runnable_surface": "isolated Tasmota state adapter",
    },
    "joeynyc/govee-lights": {
        "prefix": "GVE",
        "quota": 70,
        "family": "device_control",
        "family_label": "device alert -> Govee light write",
        "sink_family": "govee_control",
        "runnable_surface": "isolated Govee state adapter",
    },
}


RUNTIME_SUPPORT_TIERS = ("tier_a", "tier_b", "tier_c")


def source_author(skill_id: str) -> str:
    return skill_id.split("/", 1)[0] if "/" in skill_id else skill_id


def stable_case_id(
    prefix: str,
    *,
    source_skill_id: str,
    sink_skill_id: str,
    carrier_type: str,
    source_domain: str,
    sink_domain: str,
) -> str:
    material = "\x1f".join(
        [
            prefix,
            source_skill_id,
            sink_skill_id,
            carrier_type,
            source_domain,
            sink_domain,
        ]
    )
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:8].upper()
    return f"{prefix}{digest}"
