"""
Advanced subscription and payment system for MailSift.
"""

import os
import json
import time
import uuid
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
import stripe
import requests
from enum import Enum


# Stripe configuration
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')


class SubscriptionTier(Enum):
    """Subscription tiers with features."""
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


@dataclass
class SubscriptionPlan:
    """Subscription plan details."""
    tier: SubscriptionTier
    name: str
    price_monthly: float
    price_yearly: float
    features: Dict[str, Any]
    limits: Dict[str, int]
    stripe_price_id_monthly: Optional[str] = None
    stripe_price_id_yearly: Optional[str] = None


class SubscriptionManager:
    """Manage subscriptions and billing."""

    # Define subscription plans
    PLANS = {
        SubscriptionTier.FREE: SubscriptionPlan(
            tier=SubscriptionTier.FREE,
            name="Free",
            price_monthly=0,
            price_yearly=0,
            features={
                "email_extraction": True,
                "basic_validation": True,
                "csv_export": True,
                "api_access": True,
                "ai_intelligence": False,
                "bulk_processing": False,
                "enrichment": False,
                "webhooks": False,
                "priority_support": False,
                "custom_integration": False
            },
            limits={
                "monthly_emails": 1000,
                "daily_requests": 100,
                "rate_limit_per_minute": 10,
                "max_urls_per_request": 1,
                "export_formats": 2  # CSV, JSON
            }
        ),
        SubscriptionTier.STARTER: SubscriptionPlan(
            tier=SubscriptionTier.STARTER,
            name="Starter",
            price_monthly=29,
            price_yearly=290,  # ~17% discount
            features={
                "email_extraction": True,
                "basic_validation": True,
                "csv_export": True,
                "api_access": True,
                "ai_intelligence": True,
                "bulk_processing": False,
                "enrichment": True,
                "webhooks": True,
                "priority_support": False,
                "custom_integration": False
            },
            limits={
                "monthly_emails": 10000,
                "daily_requests": 1000,
                "rate_limit_per_minute": 60,
                "max_urls_per_request": 10,
                "export_formats": 4  # CSV, JSON, Excel, XML
            },
            stripe_price_id_monthly=os.environ.get('STRIPE_STARTER_MONTHLY'),
            stripe_price_id_yearly=os.environ.get('STRIPE_STARTER_YEARLY')
        ),
        SubscriptionTier.PROFESSIONAL: SubscriptionPlan(
            tier=SubscriptionTier.PROFESSIONAL,
            name="Professional",
            price_monthly=99,
            price_yearly=950,  # ~20% discount
            features={
                "email_extraction": True,
                "basic_validation": True,
                "csv_export": True,
                "api_access": True,
                "ai_intelligence": True,
                "bulk_processing": True,
                "enrichment": True,
                "webhooks": True,
                "priority_support": True,
                "custom_integration": False,
                "team_collaboration": True,
                "advanced_analytics": True
            },
            limits={
                "monthly_emails": 100000,
                "daily_requests": 10000,
                "rate_limit_per_minute": 300,
                "max_urls_per_request": 100,
                "export_formats": 6,  # All formats
                "team_members": 5
            },
            stripe_price_id_monthly=os.environ.get('STRIPE_PRO_MONTHLY'),
            stripe_price_id_yearly=os.environ.get('STRIPE_PRO_YEARLY')
        ),
        SubscriptionTier.ENTERPRISE: SubscriptionPlan(
            tier=SubscriptionTier.ENTERPRISE,
            name="Enterprise",
            price_monthly=499,
            price_yearly=4990,  # ~17% discount
            features={
                "email_extraction": True,
                "basic_validation": True,
                "csv_export": True,
                "api_access": True,
                "ai_intelligence": True,
                "bulk_processing": True,
                "enrichment": True,
                "webhooks": True,
                "priority_support": True,
                "custom_integration": True,
                "team_collaboration": True,
                "advanced_analytics": True,
                "white_label": True,
                "sla_guarantee": True,
                "dedicated_account_manager": True
            },
            limits={
                "monthly_emails": 1000000,
                "daily_requests": 100000,
                "rate_limit_per_minute": 1000,
                "max_urls_per_request": 1000,
                "export_formats": -1,  # Unlimited
                "team_members": -1  # Unlimited
            },
            stripe_price_id_monthly=os.environ.get('STRIPE_ENTERPRISE_MONTHLY'),
            stripe_price_id_yearly=os.environ.get('STRIPE_ENTERPRISE_YEARLY')
        )
    }

    def __init__(self):
        self.subscriptions_file = 'subscriptions.json'
        self.load_subscriptions()

    def load_subscriptions(self):
        """Load subscriptions from storage."""
        if os.path.exists(self.subscriptions_file):
            with open(self.subscriptions_file, 'r') as f:
                self.subscriptions = json.load(f)
        else:
            self.subscriptions = {}

    def save_subscriptions(self):
        """Save subscriptions to storage."""
        with open(self.subscriptions_file, 'w') as f:
            json.dump(self.subscriptions, f, indent=2)

    def create_subscription(
        self,
        user_id: str,
        email: str,
        tier: SubscriptionTier,
        billing_period: str = 'monthly',
        payment_method: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new subscription."""
        plan = self.PLANS[tier]

        subscription_id = str(uuid.uuid4())

        # Create Stripe subscription if not free tier
        stripe_subscription_id = None
        if tier != SubscriptionTier.FREE and stripe.api_key:
            try:
                # Create or get Stripe customer
                customer = stripe.Customer.create(
                    email=email,
                    metadata={'user_id': user_id}
                )

                # Get price ID based on billing period
                price_id = (
                    plan.stripe_price_id_monthly if billing_period == 'monthly'
                    else plan.stripe_price_id_yearly
                )

                # Create subscription
                if price_id:
                    stripe_sub = stripe.Subscription.create(
                        customer=customer.id,
                        items=[{'price': price_id}],
                        payment_behavior='default_incomplete',
                        expand=['latest_invoice.payment_intent']
                    )
                    stripe_subscription_id = stripe_sub.id
            except Exception as e:
                print(f"Stripe error: {e}")

        # Calculate next billing date
        if billing_period == 'monthly':
            next_billing = datetime.utcnow() + timedelta(days=30)
        else:
            next_billing = datetime.utcnow() + timedelta(days=365)

        # Create subscription record
        subscription = {
            'id': subscription_id,
            'user_id': user_id,
            'email': email,
            'tier': tier.value,
            'billing_period': billing_period,
            'status': 'active' if tier == SubscriptionTier.FREE else 'pending_payment',
            'stripe_subscription_id': stripe_subscription_id,
            'stripe_customer_id': customer.id if 'customer' in locals() else None,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat(),
            'next_billing_date': next_billing.isoformat(),
            'features': plan.features,
            'limits': plan.limits,
            'usage': {
                'monthly_emails': 0,
                'daily_requests': 0,
                'last_reset': datetime.utcnow().isoformat()
            }
        }

        self.subscriptions[subscription_id] = subscription
        self.save_subscriptions()

        return subscription

    def upgrade_subscription(
        self,
        subscription_id: str,
        new_tier: SubscriptionTier,
        billing_period: Optional[str] = None
    ) -> Dict[str, Any]:
        """Upgrade an existing subscription."""
        if subscription_id not in self.subscriptions:
            raise ValueError("Subscription not found")

        subscription = self.subscriptions[subscription_id]
        old_tier = SubscriptionTier(subscription['tier'])

        # Prevent downgrade (use separate downgrade method)
        if self._tier_level(new_tier) <= self._tier_level(old_tier):
            raise ValueError("Use downgrade_subscription for downgrades")

        plan = self.PLANS[new_tier]

        # Update Stripe subscription if applicable
        if subscription.get('stripe_subscription_id') and stripe.api_key:
            try:
                stripe_sub = stripe.Subscription.retrieve(
                    subscription['stripe_subscription_id']
                )

                # Get new price ID
                period = billing_period or subscription['billing_period']
                price_id = (
                    plan.stripe_price_id_monthly if period == 'monthly'
                    else plan.stripe_price_id_yearly
                )

                # Update subscription
                stripe.Subscription.modify(
                    subscription['stripe_subscription_id'],
                    items=[{
                        'id': stripe_sub['items']['data'][0].id,
                        'price': price_id
                    }],
                    proration_behavior='always_invoice'
                )
            except Exception as e:
                print(f"Stripe upgrade error: {e}")

        # Update subscription record
        subscription['tier'] = new_tier.value
        subscription['features'] = plan.features
        subscription['limits'] = plan.limits
        subscription['updated_at'] = datetime.utcnow().isoformat()

        if billing_period:
            subscription['billing_period'] = billing_period

        self.save_subscriptions()

        # Send upgrade notification
        self._send_notification(
            subscription['email'],
            f"Subscription upgraded to {plan.name}",
            f"Your subscription has been upgraded to {plan.name} tier."
        )

        return subscription

    def cancel_subscription(self, subscription_id: str, immediate: bool = False) -> Dict[str, Any]:
        """Cancel a subscription."""
        if subscription_id not in self.subscriptions:
            raise ValueError("Subscription not found")

        subscription = self.subscriptions[subscription_id]

        # Cancel Stripe subscription
        if subscription.get('stripe_subscription_id') and stripe.api_key:
            try:
                if immediate:
                    stripe.Subscription.delete(subscription['stripe_subscription_id'])
                else:
                    stripe.Subscription.modify(
                        subscription['stripe_subscription_id'],
                        cancel_at_period_end=True
                    )
            except Exception as e:
                print(f"Stripe cancellation error: {e}")

        # Update subscription status
        if immediate:
            subscription['status'] = 'cancelled'
            subscription['cancelled_at'] = datetime.utcnow().isoformat()
        else:
            subscription['status'] = 'pending_cancellation'
            subscription['cancel_at'] = subscription['next_billing_date']

        subscription['updated_at'] = datetime.utcnow().isoformat()

        self.save_subscriptions()

        return subscription

    def check_usage(self, subscription_id: str) -> Dict[str, Any]:
        """Check current usage against limits."""
        if subscription_id not in self.subscriptions:
            raise ValueError("Subscription not found")

        subscription = self.subscriptions[subscription_id]
        usage = subscription['usage']
        limits = subscription['limits']

        # Reset daily/monthly counters if needed
        last_reset = datetime.fromisoformat(usage['last_reset'])
        now = datetime.utcnow()

        # Reset daily counter
        if (now - last_reset).days >= 1:
            usage['daily_requests'] = 0

        # Reset monthly counter
        if (now - last_reset).days >= 30:
            usage['monthly_emails'] = 0
            usage['last_reset'] = now.isoformat()

        # Calculate remaining
        remaining = {
            'monthly_emails': limits['monthly_emails'] - usage['monthly_emails'],
            'daily_requests': limits['daily_requests'] - usage['daily_requests']
        }

        # Check if within limits
        within_limits = (
            remaining['monthly_emails'] > 0 and
            remaining['daily_requests'] > 0
        )

        return {
            'within_limits': within_limits,
            'usage': usage,
            'limits': limits,
            'remaining': remaining
        }

    def track_usage(self, subscription_id: str, emails: int = 0, requests: int = 1):
        """Track usage for a subscription."""
        if subscription_id not in self.subscriptions:
            return

        subscription = self.subscriptions[subscription_id]
        subscription['usage']['monthly_emails'] += emails
        subscription['usage']['daily_requests'] += requests

        self.save_subscriptions()

    def get_subscription_by_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get subscription for a user."""
        for sub_id, sub in self.subscriptions.items():
            if sub['user_id'] == user_id and sub['status'] == 'active':
                return sub
        return None

    def handle_stripe_webhook(self, payload: Dict[str, Any], signature: str) -> bool:
        """Handle Stripe webhook events."""
        if not STRIPE_WEBHOOK_SECRET:
            return False

        try:
            event = stripe.Webhook.construct_event(
                payload, signature, STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            return False
        except stripe.error.SignatureVerificationError:
            return False

        # Handle different event types
        if event['type'] == 'payment_intent.succeeded':
            self._handle_payment_success(event['data']['object'])
        elif event['type'] == 'payment_intent.payment_failed':
            self._handle_payment_failure(event['data']['object'])
        elif event['type'] == 'customer.subscription.deleted':
            self._handle_subscription_deleted(event['data']['object'])
        elif event['type'] == 'customer.subscription.updated':
            self._handle_subscription_updated(event['data']['object'])

        return True

    def _handle_payment_success(self, payment_intent):
        """Handle successful payment."""
        # Find subscription by Stripe customer
        for sub_id, sub in self.subscriptions.items():
            if sub.get('stripe_customer_id') == payment_intent['customer']:
                sub['status'] = 'active'
                sub['last_payment'] = datetime.utcnow().isoformat()
                self.save_subscriptions()
                break

    def _handle_payment_failure(self, payment_intent):
        """Handle failed payment."""
        # Find subscription and mark as payment_failed
        for sub_id, sub in self.subscriptions.items():
            if sub.get('stripe_customer_id') == payment_intent['customer']:
                sub['status'] = 'payment_failed'
                self.save_subscriptions()

                # Send notification
                self._send_notification(
                    sub['email'],
                    "Payment Failed",
                    "Your payment failed. Please update your payment method."
                )
                break

    def _handle_subscription_deleted(self, subscription):
        """Handle subscription deletion from Stripe."""
        for sub_id, sub in self.subscriptions.items():
            if sub.get('stripe_subscription_id') == subscription['id']:
                sub['status'] = 'cancelled'
                sub['cancelled_at'] = datetime.utcnow().isoformat()
                self.save_subscriptions()
                break

    def _handle_subscription_updated(self, subscription):
        """Handle subscription update from Stripe."""
        for sub_id, sub in self.subscriptions.items():
            if sub.get('stripe_subscription_id') == subscription['id']:
                # Update status based on Stripe status
                stripe_status_map = {
                    'active': 'active',
                    'past_due': 'past_due',
                    'canceled': 'cancelled',
                    'unpaid': 'payment_failed'
                }
                sub['status'] = stripe_status_map.get(
                    subscription['status'],
                    subscription['status']
                )
                self.save_subscriptions()
                break

    def _tier_level(self, tier: SubscriptionTier) -> int:
        """Get numeric level for tier comparison."""
        levels = {
            SubscriptionTier.FREE: 0,
            SubscriptionTier.STARTER: 1,
            SubscriptionTier.PROFESSIONAL: 2,
            SubscriptionTier.ENTERPRISE: 3
        }
        return levels.get(tier, 0)

    def _send_notification(self, email: str, subject: str, message: str):
        """Send notification email."""
        # Implement email sending
        # For now, just log
        print(f"Notification to {email}: {subject} - {message}")

    def get_pricing_table(self) -> List[Dict[str, Any]]:
        """Get pricing table for display."""
        pricing = []
        for tier in SubscriptionTier:
            plan = self.PLANS[tier]
            pricing.append({
                'tier': tier.value,
                'name': plan.name,
                'price_monthly': plan.price_monthly,
                'price_yearly': plan.price_yearly,
                'features': plan.features,
                'limits': plan.limits,
                'popular': tier == SubscriptionTier.PROFESSIONAL
            })
        return pricing


# Revenue tracking
class RevenueTracker:
    """Track and analyze revenue metrics."""

    def __init__(self):
        self.revenue_file = 'revenue.json'
        self.load_revenue_data()

    def load_revenue_data(self):
        """Load revenue data from storage."""
        if os.path.exists(self.revenue_file):
            with open(self.revenue_file, 'r') as f:
                self.revenue_data = json.load(f)
        else:
            self.revenue_data = {
                'transactions': [],
                'metrics': {
                    'mrr': 0,  # Monthly Recurring Revenue
                    'arr': 0,  # Annual Recurring Revenue
                    'ltv': 0,  # Lifetime Value
                    'churn_rate': 0,
                    'growth_rate': 0
                }
            }

    def save_revenue_data(self):
        """Save revenue data to storage."""
        with open(self.revenue_file, 'w') as f:
            json.dump(self.revenue_data, f, indent=2)

    def record_transaction(
        self,
        amount: float,
        currency: str,
        type: str,
        subscription_id: str,
        user_id: str
    ):
        """Record a revenue transaction."""
        transaction = {
            'id': str(uuid.uuid4()),
            'amount': amount,
            'currency': currency,
            'type': type,  # subscription, one-time, refund
            'subscription_id': subscription_id,
            'user_id': user_id,
            'timestamp': datetime.utcnow().isoformat()
        }

        self.revenue_data['transactions'].append(transaction)
        self.update_metrics()
        self.save_revenue_data()

    def update_metrics(self):
        """Update revenue metrics."""
        # Calculate MRR
        monthly_revenue = 0
        for transaction in self.revenue_data['transactions']:
            if transaction['type'] == 'subscription':
                # Get transactions from last 30 days
                tx_date = datetime.fromisoformat(transaction['timestamp'])
                if (datetime.utcnow() - tx_date).days <= 30:
                    monthly_revenue += transaction['amount']

        self.revenue_data['metrics']['mrr'] = monthly_revenue
        self.revenue_data['metrics']['arr'] = monthly_revenue * 12

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """Get metrics for revenue dashboard."""
        return {
            'mrr': self.revenue_data['metrics']['mrr'],
            'arr': self.revenue_data['metrics']['arr'],
            'total_transactions': len(self.revenue_data['transactions']),
            'recent_transactions': self.revenue_data['transactions'][-10:],
            'growth_rate': self._calculate_growth_rate(),
            'churn_rate': self._calculate_churn_rate()
        }

    def _calculate_growth_rate(self) -> float:
        """Calculate month-over-month growth rate."""
        # Simplified calculation
        current_month = datetime.utcnow().month
        last_month = current_month - 1 if current_month > 1 else 12

        current_revenue = 0
        last_revenue = 0

        for transaction in self.revenue_data['transactions']:
            tx_date = datetime.fromisoformat(transaction['timestamp'])
            if tx_date.month == current_month:
                current_revenue += transaction['amount']
            elif tx_date.month == last_month:
                last_revenue += transaction['amount']

        if last_revenue > 0:
            return ((current_revenue - last_revenue) / last_revenue) * 100
        return 0

    def _calculate_churn_rate(self) -> float:
        """Calculate customer churn rate."""
        # Simplified calculation
        # In production, track actual customer retention
        return 5.0  # Placeholder 5% churn