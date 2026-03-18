"""
Python quality fixture for integration testing.

Intentionally contains code quality, async, and security issues
that should trigger specific detectors.
"""

import asyncio
import json
import os
import pickle
import ssl
import time

import requests
import yaml


# ── Mutable Default Args ──────────────────────────────────────────────
# Should trigger: mutable-default-args

def collect_items(items=[]):
    """Mutable default list — shared across all calls."""
    items.append("new")
    return items


def build_registry(registry={}):
    """Mutable default dict — accumulates across calls."""
    registry["key"] = "value"
    return registry


def gather_ids(seen=set()):
    """Mutable default set — grows silently."""
    seen.add(42)
    return seen


# ── Broad Exception ──────────────────────────────────────────────────
# Should trigger: broad-exception

def load_config(path):
    """Catches everything — hides real errors."""
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return {}


def fetch_data(url):
    """Catches Exception — too broad."""
    try:
        response = requests.get(url)
        return response.json()
    except Exception:
        return None


def parse_payload(raw):
    """Catches BaseException — even worse."""
    try:
        return json.loads(raw)
    except BaseException:
        return {}


# ── Sync in Async ────────────────────────────────────────────────────
# Should trigger: sync-in-async

async def slow_handler(delay):
    """Blocking sleep inside async — freezes the event loop."""
    time.sleep(delay)
    return "done"


async def blocking_fetch(url):
    """Blocking HTTP call inside async context."""
    response = requests.get(url)
    return response.text


async def shell_runner(cmd):
    """Blocking subprocess inside async context."""
    os.system(cmd)


# ── Missing Await ────────────────────────────────────────────────────
# Should trigger: missing-await

async def async_fetch(url):
    """An async helper that fetches data."""
    await asyncio.sleep(0.1)
    return {"url": url}


async def orchestrator():
    """Calls async functions without await — gets coroutine objects instead."""
    data = async_fetch("/api/data")
    return data


async def pipeline():
    """Missing await on async I/O call."""
    result = async_fetch("/api/pipeline")
    return result


# ── Insecure TLS ─────────────────────────────────────────────────────
# Should trigger: InsecureTlsDetector

def download_insecure(url):
    """Certificate verification disabled — MitM risk."""
    return requests.get(url, verify=False)


def make_bad_context():
    """SSL context with no cert validation."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── Insecure Deserialization ─────────────────────────────────────────
# Should trigger: insecure-deserialize, PickleDeserializationDetector

def load_yaml_unsafe(data):
    """yaml.load without SafeLoader — can execute arbitrary code."""
    return yaml.load(data)


def load_pickle_unsafe(user_data):
    """pickle.loads on untrusted data — arbitrary code execution."""
    return pickle.loads(user_data)


def dangerous_eval(expr):
    """eval on user input — direct code execution."""
    return eval(expr)


# ── Additional quality issues ────────────────────────────────────────

def deeply_nested(x):
    """Deep nesting — hard to follow."""
    if x > 0:
        if x > 10:
            if x > 100:
                if x > 1000:
                    if x > 10000:
                        return "very big"
                    return "big"
                return "medium"
            return "small"
        return "tiny"
    return "zero"


MAGIC_TIMEOUT = 86400
MAGIC_RETRIES = 3
MAGIC_PORT = 8080


def process(data):
    """Uses magic numbers."""
    if len(data) > 256:
        return data[:256]
    if len(data) < 16:
        return None
    return data


class KitchenSink:
    """God class — does everything."""

    def __init__(self):
        self.db = None
        self.cache = {}
        self.logger = None
        self.mailer = None
        self.queue = None
        self.config = {}
        self.metrics = {}
        self.sessions = {}
        self.scheduler = None
        self.validator = None

    def connect_db(self): pass
    def disconnect_db(self): pass
    def query_db(self, sql): pass
    def cache_get(self, key): pass
    def cache_set(self, key, val): pass
    def cache_invalidate(self): pass
    def log_info(self, msg): pass
    def log_error(self, msg): pass
    def send_email(self, to, body): pass
    def send_sms(self, to, body): pass
    def enqueue(self, task): pass
    def dequeue(self): pass
    def validate_input(self, data): pass
    def sanitize_output(self, data): pass
    def collect_metrics(self): pass
    def report_metrics(self): pass
    def create_session(self, user): pass
    def destroy_session(self, sid): pass
    def schedule_job(self, job): pass
    def cancel_job(self, jid): pass
