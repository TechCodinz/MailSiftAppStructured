"""
MailSift Ultra - Next-generation email intelligence platform.
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response
from flask_cors import CORS
import os
import json
from datetime import datetime
import logging
from dotenv import load_dotenv
from typing import Union, Any

# Load environment variables
load_dotenv()

# Import existing modules
from app import (
    extract_emails_from_text,
    extract_emails_from_html,
    group_by_provider,
    detect_provider,
    extract_domain,
    classify_expertise
)
from file_parsing import extract_text_from_file
from payments import (
    record_payment,
    mark_verified,
    list_payments,
    verify_admin_key
)

# Import new modules
from ai_intelligence import EmailIntelligenceEngine
from api_v2 import init_api, api_key_manager
from subscription_system import SubscriptionManager, RevenueTracker, SubscriptionTier

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('MAILSIFT_SECRET', 'dev-secret-key-change-in-production')

# Enable CORS for API access
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Configure app
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('mailsift')

# Initialize services
ai_engine = EmailIntelligenceEngine(api_keys={
    'openai': os.environ.get('OPENAI_API_KEY'),
    'clearbit': os.environ.get('CLEARBIT_API_KEY')
})
subscription_manager = SubscriptionManager()
revenue_tracker = RevenueTracker()

# Register API blueprint
init_api(app)


@app.route('/')
def index() -> str:
    """Enhanced landing page with subscription options."""
    # Check user's subscription status
    user_id = session.get('user_id')
    subscription = None
    if user_id:
        subscription = subscription_manager.get_subscription_by_user(user_id)

    # Get pricing for display
    pricing = subscription_manager.get_pricing_table()

    return render_template(
        'index_v2.html',
        subscription=subscription,
        pricing=pricing
    )


@app.route('/extract', methods=['POST'])
def extract_emails_enhanced() -> tuple[Response, int]:
    """Enhanced email extraction with AI intelligence."""
    # Check subscription limits
    user_id = session.get('user_id')
    if not user_id:
        user_id = session.get('session_id', session.sid if hasattr(session, 'sid') else 'anonymous')

    subscription = subscription_manager.get_subscription_by_user(user_id)
    if not subscription:
        # Create free subscription for new users
        email = session.get('email', f"{user_id}@temp.local")
        subscription = subscription_manager.create_subscription(
            user_id, email, SubscriptionTier.FREE
        )

    # Check usage limits
    usage_check = subscription_manager.check_usage(subscription['id'])
    if not usage_check['within_limits']:
        return jsonify({
            'error': 'Usage limit exceeded',
            'limits': usage_check['limits'],
            'usage': usage_check['usage']
        }), 429

    # Get input data
    text = request.form.get('text_input', '')
    urls = request.form.getlist('urls')
    file = request.files.get('file_input')

    # Process file if uploaded
    if file:
        text += extract_text_from_file(file.stream, file.filename)

    # Extract emails
    all_emails = []
    if text:
        valid, _ = extract_emails_from_text(text)
        all_emails.extend(valid)

    # Fetch from URLs
    if urls:
        import requests as req
        from concurrent.futures import ThreadPoolExecutor

        def fetch_url(url):
            try:
                r = req.get(url, timeout=5)
                valid, _ = extract_emails_from_html(r.text)
                return valid
            except:
                return []

        with ThreadPoolExecutor(max_workers=5) as executor:
            results = executor.map(fetch_url, urls[:10])
            for result in results:
                all_emails.extend(result)

    # Remove duplicates
    all_emails = list(set(all_emails))

    # Apply AI intelligence if subscription allows
    intelligence_results = []
    if subscription['features'].get('ai_intelligence'):
        intelligence_results = ai_engine.bulk_analyze(all_emails, text)

    # Track usage
    subscription_manager.track_usage(
        subscription['id'],
        emails=len(all_emails),
        requests=1
    )

    # Store in session
    session['extracted_emails'] = all_emails
    session['intelligence_results'] = [
        {
            'email': r.email,
            'deliverability': r.deliverability_score,
            'risk': r.risk_score,
            'type': r.email_type,
            'intent': r.intent,
            'social': r.social_profiles
        }
        for r in intelligence_results
    ] if intelligence_results else []

    # Prepare response
    response_data = {
        'success': True,
        'count': len(all_emails),
        'emails': all_emails,
        'intelligence': session.get('intelligence_results', []),
        'usage': usage_check['usage'],
        'remaining': usage_check['remaining']
    }

    return jsonify(response_data)


@app.route('/subscribe', methods=['POST'])
def subscribe() -> tuple[Response, int]:
    """Handle subscription creation."""
    data = request.get_json()

    email = data.get('email')
    tier = data.get('tier', 'free')
    billing_period = data.get('billing_period', 'monthly')

    if not email:
        return jsonify({'error': 'Email required'}), 400

    # Create or get user
    user_id = session.get('user_id')
    if not user_id:
        user_id = f"user_{email.replace('@', '_').replace('.', '_')}"
        session['user_id'] = user_id

    session['email'] = email

    try:
        # Create subscription
        subscription = subscription_manager.create_subscription(
            user_id,
            email,
            SubscriptionTier(tier),
            billing_period
        )

        # Create API key for the user
        api_key = api_key_manager.create_key(user_id, tier)

        # Return subscription details
        return jsonify({
            'success': True,
            'subscription': subscription,
            'api_key': api_key,
            'payment_required': tier != 'free',
            'checkout_url': f"/checkout/{subscription['id']}" if tier != 'free' else None
        })
    except Exception as e:
        logger.error(f"Subscription error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/checkout/<subscription_id>')
def checkout(subscription_id: str) -> Union[str, Response]:
    """Stripe checkout page."""
    # Get subscription
    subscription = subscription_manager.subscriptions.get(subscription_id)
    if not subscription:
        return redirect(url_for('index'))

    # Get Stripe payment intent
    client_secret = None
    if subscription.get('stripe_subscription_id'):
        import stripe
        stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
        try:
            stripe_sub = stripe.Subscription.retrieve(
                subscription['stripe_subscription_id'],
                expand=['latest_invoice.payment_intent']
            )
            if stripe_sub.latest_invoice and stripe_sub.latest_invoice.payment_intent:
                client_secret = stripe_sub.latest_invoice.payment_intent.client_secret
        except Exception as e:
            logger.error(f"Stripe error: {e}")

    return render_template(
        'checkout.html',
        subscription=subscription,
        stripe_public_key=os.environ.get('STRIPE_PUBLIC_KEY'),
        client_secret=client_secret
    )


@app.route('/dashboard')
def dashboard() -> Union[str, Response]:
    """User dashboard with analytics."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index'))

    # Get user's subscription
    subscription = subscription_manager.get_subscription_by_user(user_id)
    if not subscription:
        return redirect(url_for('index'))

    # Get usage statistics
    usage_check = subscription_manager.check_usage(subscription['id'])

    # Get extraction history (simplified - use database in production)
    history = session.get('extraction_history', [])

    # Get API keys
    user_api_keys = [
        key_data for key, key_data in api_key_manager.keys.items()
        if key_data['user_id'] == user_id
    ]

    return render_template(
        'dashboard.html',
        subscription=subscription,
        usage=usage_check,
        history=history,
        api_keys=user_api_keys
    )


@app.route('/admin/dashboard')
def admin_dashboard() -> Union[str, tuple[Response, int]]:
    """Admin dashboard with revenue analytics."""
    # Check admin authentication
    auth = request.authorization
    if not auth or not verify_admin_key(auth.password):
        return jsonify({'error': 'Unauthorized'}), 401

    # Get revenue metrics
    revenue_metrics = revenue_tracker.get_dashboard_metrics()

    # Get subscription statistics
    total_subscriptions = len(subscription_manager.subscriptions)
    active_subscriptions = sum(
        1 for s in subscription_manager.subscriptions.values()
        if s['status'] == 'active'
    )

    # Get tier distribution
    tier_distribution = {}
    for sub in subscription_manager.subscriptions.values():
        tier = sub['tier']
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

    return render_template(
        'admin_dashboard.html',
        revenue=revenue_metrics,
        total_subscriptions=total_subscriptions,
        active_subscriptions=active_subscriptions,
        tier_distribution=tier_distribution
    )


@app.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook() -> tuple[Response, int]:
    """Handle Stripe webhooks."""
    payload = request.data
    signature = request.headers.get('Stripe-Signature')

    if subscription_manager.handle_stripe_webhook(payload, signature):
        return jsonify({'success': True}), 200
    else:
        return jsonify({'error': 'Invalid webhook'}), 400


@app.route('/api/v2/health')
def api_health() -> Response:
    """API health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'version': '2.0.0',
        'timestamp': datetime.utcnow().isoformat()
    })


@app.errorhandler(404)
def not_found(error: Exception) -> tuple[Response, int]:
    """Handle 404 errors."""
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error: Exception) -> tuple[Response, int]:
    """Handle 500 errors."""
    logger.error(f"Internal error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    # Check for required environment variables
    required_vars = [
        'MAILSIFT_SECRET',
        'STRIPE_SECRET_KEY',
        'STRIPE_PUBLIC_KEY'
    ]

    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars and os.environ.get('FLASK_ENV') == 'production':
        logger.error(f"Missing required environment variables: {missing_vars}")
        exit(1)

    # Run the application
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'

    logger.info(f"Starting MailSift Ultra on port {port}")
    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug
    )