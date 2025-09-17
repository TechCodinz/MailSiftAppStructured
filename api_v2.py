"""
Advanced API module for MailSift with monetization and rate limiting.
"""

from flask import Blueprint, request, jsonify, g, Response, Flask
from functools import wraps
import hashlib
import hmac
import time
import uuid
import json
import os
from typing import Dict, Any, Optional, List, Callable
from dataclasses import asdict
import redis
from datetime import datetime, timedelta
import jwt

from ai_intelligence import EmailIntelligenceEngine, EmailIntelligence
from app import extract_emails_from_text, extract_emails_from_html

# Initialize API blueprint
api_v2 = Blueprint('api_v2', __name__, url_prefix='/api/v2')

# Redis connection for rate limiting and caching
try:
    redis_client = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379'))
    REDIS_AVAILABLE = True
except:
    redis_client = None
    REDIS_AVAILABLE = False

# JWT secret
JWT_SECRET = os.environ.get('JWT_SECRET', os.environ.get('MAILSIFT_SECRET', 'dev-secret-key'))

# Initialize AI engine
ai_engine = EmailIntelligenceEngine(api_keys={
    'openai': os.environ.get('OPENAI_API_KEY'),
    'clearbit': os.environ.get('CLEARBIT_API_KEY')
})


class APIKeyManager:
    """Manage API keys and usage tracking."""

    def __init__(self):
        self.keys_file = 'api_keys.json'
        self.load_keys()

    def load_keys(self):
        """Load API keys from storage."""
        if os.path.exists(self.keys_file):
            with open(self.keys_file, 'r') as f:
                self.keys = json.load(f)
        else:
            self.keys = {}

    def save_keys(self):
        """Save API keys to storage."""
        with open(self.keys_file, 'w') as f:
            json.dump(self.keys, f, indent=2)

    def create_key(self, user_id: str, tier: str = 'free') -> str:
        """Create a new API key for a user."""
        key = f"msk_{uuid.uuid4().hex}"

        self.keys[key] = {
            'user_id': user_id,
            'tier': tier,
            'created_at': datetime.utcnow().isoformat(),
            'usage': {
                'requests': 0,
                'emails_processed': 0,
                'last_used': None
            },
            'limits': self._get_tier_limits(tier),
            'active': True
        }

        self.save_keys()
        return key

    def validate_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Validate an API key and return its data."""
        if key not in self.keys:
            return None

        key_data = self.keys[key]

        if not key_data.get('active'):
            return None

        return key_data

    def track_usage(self, key: str, emails_count: int = 0):
        """Track API key usage."""
        if key in self.keys:
            self.keys[key]['usage']['requests'] += 1
            self.keys[key]['usage']['emails_processed'] += emails_count
            self.keys[key]['usage']['last_used'] = datetime.utcnow().isoformat()
            self.save_keys()

    def check_limits(self, key: str, emails_count: int = 0) -> bool:
        """Check if API key is within usage limits."""
        key_data = self.keys.get(key)
        if not key_data:
            return False

        limits = key_data['limits']
        usage = key_data['usage']

        # Check daily request limit
        if usage['requests'] >= limits['daily_requests']:
            return False

        # Check monthly email limit
        if usage['emails_processed'] + emails_count > limits['monthly_emails']:
            return False

        return True

    def _get_tier_limits(self, tier: str) -> Dict[str, int]:
        """Get usage limits for a tier."""
        tiers = {
            'free': {
                'daily_requests': 100,
                'monthly_emails': 1000,
                'rate_limit': 10  # requests per minute
            },
            'starter': {
                'daily_requests': 1000,
                'monthly_emails': 10000,
                'rate_limit': 60
            },
            'professional': {
                'daily_requests': 10000,
                'monthly_emails': 100000,
                'rate_limit': 300
            },
            'enterprise': {
                'daily_requests': 100000,
                'monthly_emails': 1000000,
                'rate_limit': 1000
            }
        }
        return tiers.get(tier, tiers['free'])


# Initialize API key manager
api_key_manager = APIKeyManager()


def require_api_key(f: Callable) -> Callable:
    """Decorator to require API key authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for API key in headers or query params
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')

        if not api_key:
            return jsonify({'error': 'API key required'}), 401

        # Validate API key
        key_data = api_key_manager.validate_key(api_key)
        if not key_data:
            return jsonify({'error': 'Invalid API key'}), 401

        # Store key data in g for use in the route
        g.api_key = api_key
        g.api_key_data = key_data

        return f(*args, **kwargs)

    return decorated_function


def rate_limit(f: Callable) -> Callable:
    """Decorator for rate limiting based on API tier."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not REDIS_AVAILABLE:
            return f(*args, **kwargs)

        api_key = g.get('api_key')
        if not api_key:
            return jsonify({'error': 'API key required'}), 401

        key_data = g.get('api_key_data')
        limit = key_data['limits']['rate_limit']

        # Use Redis for rate limiting
        key = f"rate_limit:{api_key}"
        try:
            current = redis_client.incr(key)
            if current == 1:
                redis_client.expire(key, 60)  # 1 minute window

            if current > limit:
                return jsonify({
                    'error': 'Rate limit exceeded',
                    'limit': limit,
                    'window': '1 minute'
                }), 429
        except:
            pass  # Fail open if Redis is down

        return f(*args, **kwargs)

    return decorated_function


@api_v2.route('/extract', methods=['POST'])
@require_api_key
@rate_limit
def extract_emails() -> tuple[Response, int]:
    """Extract emails from text with AI intelligence."""
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    text = data.get('text', '')
    html = data.get('html', '')
    urls = data.get('urls', [])
    options = data.get('options', {})

    # Extract emails
    all_emails = []

    if text:
        valid, invalid = extract_emails_from_text(text)
        all_emails.extend(valid)

    if html:
        valid, invalid = extract_emails_from_html(html)
        all_emails.extend(valid)

    # Fetch from URLs if provided
    if urls:
        import requests
        from concurrent.futures import ThreadPoolExecutor

        def fetch_url(url):
            try:
                r = requests.get(url, timeout=5)
                valid, _ = extract_emails_from_html(r.text)
                return valid
            except:
                return []

        with ThreadPoolExecutor(max_workers=5) as executor:
            results = executor.map(fetch_url, urls[:10])  # Limit to 10 URLs
            for result in results:
                all_emails.extend(result)

    # Remove duplicates
    all_emails = list(set(all_emails))

    # Check usage limits
    if not api_key_manager.check_limits(g.api_key, len(all_emails)):
        return jsonify({'error': 'Usage limit exceeded'}), 429

    # Apply AI intelligence if requested
    if options.get('intelligence', False):
        context = text or html
        intelligence_results = ai_engine.bulk_analyze(all_emails, context)

        # Convert to dict format
        results = [asdict(intel) for intel in intelligence_results]
    else:
        # Basic results
        results = [{'email': email, 'valid': True} for email in all_emails]

    # Track usage
    api_key_manager.track_usage(g.api_key, len(all_emails))

    # Build response
    response = {
        'success': True,
        'count': len(results),
        'emails': results,
        'usage': {
            'emails_processed': g.api_key_data['usage']['emails_processed'] + len(all_emails),
            'monthly_limit': g.api_key_data['limits']['monthly_emails']
        }
    }

    return jsonify(response), 200


@api_v2.route('/validate', methods=['POST'])
@require_api_key
@rate_limit
def validate_emails() -> tuple[Response, int]:
    """Validate and score email addresses."""
    data = request.get_json()

    if not data or 'emails' not in data:
        return jsonify({'error': 'Emails list required'}), 400

    emails = data['emails']
    if not isinstance(emails, list):
        return jsonify({'error': 'Emails must be a list'}), 400

    # Limit number of emails per request
    emails = emails[:100]

    # Check usage limits
    if not api_key_manager.check_limits(g.api_key, len(emails)):
        return jsonify({'error': 'Usage limit exceeded'}), 429

    # Validate emails
    results = []
    for email in emails:
        intelligence = ai_engine.analyze_email(email)
        results.append({
            'email': email,
            'valid': intelligence.is_valid,
            'deliverability_score': intelligence.deliverability_score,
            'risk_score': intelligence.risk_score,
            'type': intelligence.email_type
        })

    # Track usage
    api_key_manager.track_usage(g.api_key, len(emails))

    return jsonify({
        'success': True,
        'count': len(results),
        'validations': results
    }), 200


@api_v2.route('/enrich', methods=['POST'])
@require_api_key
@rate_limit
def enrich_emails() -> tuple[Response, int]:
    """Enrich emails with additional data."""
    data = request.get_json()

    if not data or 'emails' not in data:
        return jsonify({'error': 'Emails list required'}), 400

    emails = data['emails'][:50]  # Limit to 50 per request

    # Check if user has access to enrichment (premium feature)
    if g.api_key_data['tier'] == 'free':
        return jsonify({'error': 'Enrichment requires a paid plan'}), 403

    # Check usage limits
    if not api_key_manager.check_limits(g.api_key, len(emails)):
        return jsonify({'error': 'Usage limit exceeded'}), 429

    # Enrich emails
    results = []
    for email in emails:
        intelligence = ai_engine.analyze_email(email)
        results.append({
            'email': email,
            'social_profiles': intelligence.social_profiles,
            'company_data': intelligence.company_data,
            'contact_info': intelligence.contact_info,
            'timezone': intelligence.timezone,
            'tags': intelligence.tags
        })

    # Track usage
    api_key_manager.track_usage(g.api_key, len(emails))

    return jsonify({
        'success': True,
        'count': len(results),
        'enrichments': results
    }), 200


@api_v2.route('/bulk', methods=['POST'])
@require_api_key
@rate_limit
def bulk_process() -> tuple[Response, int]:
    """Process large batches of emails asynchronously."""
    data = request.get_json()

    # Check if user has access to bulk processing
    if g.api_key_data['tier'] in ['free', 'starter']:
        return jsonify({'error': 'Bulk processing requires Professional plan or higher'}), 403

    # Create a job ID
    job_id = str(uuid.uuid4())

    # Store job data (in production, use a job queue like Celery)
    job_data = {
        'id': job_id,
        'status': 'queued',
        'created_at': datetime.utcnow().isoformat(),
        'api_key': g.api_key,
        'data': data
    }

    if REDIS_AVAILABLE:
        redis_client.setex(
            f"job:{job_id}",
            86400,  # 24 hours TTL
            json.dumps(job_data)
        )

    # In production, queue the job for processing
    # For now, return job ID for status checking

    return jsonify({
        'success': True,
        'job_id': job_id,
        'status': 'queued',
        'check_status_url': f'/api/v2/bulk/{job_id}'
    }), 202


@api_v2.route('/bulk/<job_id>', methods=['GET'])
@require_api_key
def check_bulk_status(job_id: str) -> tuple[Response, int]:
    """Check status of a bulk processing job."""
    if not REDIS_AVAILABLE:
        return jsonify({'error': 'Job tracking not available'}), 503

    job_data = redis_client.get(f"job:{job_id}")
    if not job_data:
        return jsonify({'error': 'Job not found'}), 404

    job = json.loads(job_data)

    # Check if job belongs to this API key
    if job['api_key'] != g.api_key:
        return jsonify({'error': 'Unauthorized'}), 403

    return jsonify({
        'job_id': job_id,
        'status': job['status'],
        'created_at': job['created_at']
    }), 200


@api_v2.route('/webhooks', methods=['POST'])
@require_api_key
def create_webhook() -> tuple[Response, int]:
    """Create a webhook for real-time notifications."""
    if g.api_key_data['tier'] == 'free':
        return jsonify({'error': 'Webhooks require a paid plan'}), 403

    data = request.get_json()

    if not data or 'url' not in data:
        return jsonify({'error': 'Webhook URL required'}), 400

    webhook_id = str(uuid.uuid4())

    # Store webhook (simplified - use database in production)
    webhook_data = {
        'id': webhook_id,
        'api_key': g.api_key,
        'url': data['url'],
        'events': data.get('events', ['email.extracted', 'job.completed']),
        'created_at': datetime.utcnow().isoformat(),
        'active': True
    }

    # Save webhook
    webhooks_file = 'webhooks.json'
    webhooks = {}
    if os.path.exists(webhooks_file):
        with open(webhooks_file, 'r') as f:
            webhooks = json.load(f)

    webhooks[webhook_id] = webhook_data

    with open(webhooks_file, 'w') as f:
        json.dump(webhooks, f, indent=2)

    return jsonify({
        'success': True,
        'webhook_id': webhook_id,
        'url': data['url'],
        'events': webhook_data['events']
    }), 201


@api_v2.route('/usage', methods=['GET'])
@require_api_key
def get_usage() -> tuple[Response, int]:
    """Get current API usage statistics."""
    key_data = g.api_key_data

    return jsonify({
        'tier': key_data['tier'],
        'usage': key_data['usage'],
        'limits': key_data['limits'],
        'remaining': {
            'daily_requests': key_data['limits']['daily_requests'] - key_data['usage']['requests'],
            'monthly_emails': key_data['limits']['monthly_emails'] - key_data['usage']['emails_processed']
        }
    }), 200


@api_v2.route('/keys/create', methods=['POST'])
def create_api_key() -> tuple[Response, int]:
    """Create a new API key (requires authentication)."""
    # In production, require proper authentication
    auth_header = request.headers.get('Authorization')

    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Authorization required'}), 401

    token = auth_header.split(' ')[1]

    try:
        # Decode JWT token
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        user_id = payload['user_id']
        tier = payload.get('tier', 'free')
    except:
        return jsonify({'error': 'Invalid token'}), 401

    # Create API key
    api_key = api_key_manager.create_key(user_id, tier)

    return jsonify({
        'success': True,
        'api_key': api_key,
        'tier': tier,
        'limits': api_key_manager._get_tier_limits(tier)
    }), 201


# Export blueprint
def init_api(app: Flask) -> Flask:
    """Initialize API with the Flask app."""
    app.register_blueprint(api_v2)
    return app