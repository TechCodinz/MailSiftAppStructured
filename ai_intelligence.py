"""
AI-powered email intelligence module for MailSift.
Provides advanced email analysis, classification, and enrichment.
"""

import re
import json
import hashlib
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

# AI/ML imports (optional but recommended)
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False


@dataclass
class EmailIntelligence:
    """Comprehensive email intelligence data."""
    email: str
    is_valid: bool
    deliverability_score: float  # 0-100
    risk_score: float  # 0-100
    email_type: str  # personal, business, role-based, disposable
    sentiment: str  # positive, neutral, negative
    intent: str  # sales, support, inquiry, partnership, job, other
    priority: str  # high, medium, low
    language: str
    timezone: Optional[str]
    social_profiles: Dict[str, str]
    company_data: Optional[Dict[str, Any]]
    contact_info: Dict[str, Any]
    tags: List[str]
    metadata: Dict[str, Any]


class EmailIntelligenceEngine:
    """Advanced email intelligence and enrichment engine."""

    def __init__(self, api_keys: Optional[Dict[str, str]] = None):
        self.api_keys = api_keys or {}
        self.cache = {}
        self.disposable_domains = self._load_disposable_domains()
        self.role_patterns = self._load_role_patterns()

    def _load_disposable_domains(self) -> set:
        """Load list of known disposable email domains."""
        # Common disposable email domains
        return {
            'mailinator.com', 'guerrillamail.com', '10minutemail.com',
            'tempmail.com', 'throwaway.email', 'yopmail.com',
            'maildrop.cc', 'mintemail.com', 'sharklasers.com',
            'guerrillamailblock.com', 'mailcatch.com', 'trashmail.com'
        }

    def _load_role_patterns(self) -> List[re.Pattern]:
        """Load patterns for role-based emails."""
        roles = [
            r'^(info|admin|contact|sales|support|hello|team|office|help|service)',
            r'^(marketing|hr|careers|jobs|recruitment|billing|accounts)',
            r'^(noreply|no-reply|donotreply|notifications|alerts)',
            r'^(webmaster|postmaster|hostmaster|abuse|security)'
        ]
        return [re.compile(pattern, re.IGNORECASE) for pattern in roles]

    def analyze_email(self, email: str, context: str = "") -> EmailIntelligence:
        """Perform comprehensive email analysis."""
        # Check cache
        cache_key = hashlib.md5(f"{email}{context}".encode()).hexdigest()
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Basic validation
        is_valid = self._validate_email_format(email)

        # Deliverability scoring
        deliverability_score = self._calculate_deliverability(email)

        # Risk assessment
        risk_score = self._calculate_risk_score(email)

        # Email type classification
        email_type = self._classify_email_type(email)

        # Context analysis (if provided)
        sentiment = "neutral"
        intent = "other"
        priority = "medium"
        language = "en"

        if context and OPENAI_AVAILABLE:
            analysis = self._analyze_context_with_ai(email, context)
            sentiment = analysis.get('sentiment', sentiment)
            intent = analysis.get('intent', intent)
            priority = analysis.get('priority', priority)
            language = analysis.get('language', language)

        # Social profile enrichment
        social_profiles = self._find_social_profiles(email)

        # Company data enrichment
        company_data = self._enrich_company_data(email)

        # Contact info enrichment
        contact_info = self._enrich_contact_info(email)

        # Auto-tagging
        tags = self._generate_tags(email, context)

        # Timezone detection
        timezone = self._detect_timezone(email, company_data)

        # Build intelligence object
        intelligence = EmailIntelligence(
            email=email,
            is_valid=is_valid,
            deliverability_score=deliverability_score,
            risk_score=risk_score,
            email_type=email_type,
            sentiment=sentiment,
            intent=intent,
            priority=priority,
            language=language,
            timezone=timezone,
            social_profiles=social_profiles,
            company_data=company_data,
            contact_info=contact_info,
            tags=tags,
            metadata={
                'analyzed_at': datetime.utcnow().isoformat(),
                'confidence': self._calculate_confidence(deliverability_score, risk_score)
            }
        )

        # Cache result
        self.cache[cache_key] = intelligence

        return intelligence

    def _validate_email_format(self, email: str) -> bool:
        """Advanced email format validation."""
        pattern = re.compile(
            r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        )
        return bool(pattern.match(email))

    def _calculate_deliverability(self, email: str) -> float:
        """Calculate email deliverability score."""
        score = 100.0

        # Check format
        if not self._validate_email_format(email):
            return 0.0

        # Check disposable domain
        domain = email.split('@')[1].lower()
        if domain in self.disposable_domains:
            score -= 50

        # Check role-based
        local_part = email.split('@')[0].lower()
        for pattern in self.role_patterns:
            if pattern.match(local_part):
                score -= 20
                break

        # Check domain MX records (simplified)
        if self._check_mx_records(domain):
            score += 10
        else:
            score -= 30

        # Check common typos
        if self._has_common_typos(email):
            score -= 15

        return max(0, min(100, score))

    def _calculate_risk_score(self, email: str) -> float:
        """Calculate risk score for the email."""
        risk = 0.0

        domain = email.split('@')[1].lower()
        local_part = email.split('@')[0].lower()

        # Disposable email = high risk
        if domain in self.disposable_domains:
            risk += 60

        # Role-based = medium risk
        for pattern in self.role_patterns:
            if pattern.match(local_part):
                risk += 30
                break

        # New/unknown domain = slight risk
        if not self._is_known_provider(domain):
            risk += 10

        # Suspicious patterns
        if re.search(r'\d{5,}', local_part):  # Many numbers
            risk += 15
        if len(local_part) > 30:  # Very long
            risk += 10
        if re.search(r'test|temp|fake', local_part, re.IGNORECASE):
            risk += 25

        return min(100, risk)

    def _classify_email_type(self, email: str) -> str:
        """Classify the type of email address."""
        domain = email.split('@')[1].lower()
        local_part = email.split('@')[0].lower()

        # Check if disposable
        if domain in self.disposable_domains:
            return "disposable"

        # Check if role-based
        for pattern in self.role_patterns:
            if pattern.match(local_part):
                return "role-based"

        # Check if personal (common providers)
        personal_domains = {
            'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com',
            'icloud.com', 'aol.com', 'protonmail.com'
        }
        if domain in personal_domains:
            return "personal"

        # Default to business
        return "business"

    def _analyze_context_with_ai(self, email: str, context: str) -> Dict[str, str]:
        """Use AI to analyze email context."""
        if not OPENAI_AVAILABLE or not self.api_keys.get('openai'):
            return {}

        try:
            openai.api_key = self.api_keys['openai']

            prompt = f"""
            Analyze this email and its context:
            Email: {email}
            Context: {context[:500]}

            Provide:
            1. Sentiment (positive/neutral/negative)
            2. Intent (sales/support/inquiry/partnership/job/other)
            3. Priority (high/medium/low)
            4. Language (ISO 639-1 code)

            Format: JSON object
            """

            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=150
            )

            result = json.loads(response.choices[0].message.content)
            return result

        except Exception:
            return {}

    def _find_social_profiles(self, email: str) -> Dict[str, str]:
        """Find social media profiles associated with the email."""
        profiles = {}

        # Use email to search for social profiles (simplified)
        # In production, use APIs like Clearbit, FullContact, etc.

        username = email.split('@')[0]

        # Common social media URL patterns
        social_urls = {
            'linkedin': f'https://linkedin.com/in/{username}',
            'twitter': f'https://twitter.com/{username}',
            'github': f'https://github.com/{username}'
        }

        # Verify URLs (simplified - just check if they exist)
        for platform, url in social_urls.items():
            try:
                response = requests.head(url, timeout=2)
                if response.status_code == 200:
                    profiles[platform] = url
            except:
                pass

        return profiles

    def _enrich_company_data(self, email: str) -> Optional[Dict[str, Any]]:
        """Enrich with company data based on email domain."""
        domain = email.split('@')[1].lower()

        # Skip personal emails
        if self._is_personal_email(domain):
            return None

        # Use Clearbit or similar API in production
        # This is a simplified example
        company_data = {
            'domain': domain,
            'name': domain.split('.')[0].title(),
            'industry': self._guess_industry(domain),
            'size': 'unknown',
            'location': 'unknown'
        }

        # Try to fetch more data from public APIs
        try:
            # Example: Use a company enrichment API
            if self.api_keys.get('clearbit'):
                # Implement Clearbit API call here
                pass
        except:
            pass

        return company_data

    def _enrich_contact_info(self, email: str) -> Dict[str, Any]:
        """Enrich with additional contact information."""
        contact_info = {
            'email': email,
            'email_provider': email.split('@')[1],
            'username': email.split('@')[0]
        }

        # Try to extract name from email
        username = email.split('@')[0]
        if '.' in username:
            parts = username.split('.')
            contact_info['first_name'] = parts[0].title()
            contact_info['last_name'] = parts[-1].title()
        elif '_' in username:
            parts = username.split('_')
            contact_info['first_name'] = parts[0].title()
            contact_info['last_name'] = parts[-1].title()

        return contact_info

    def _generate_tags(self, email: str, context: str) -> List[str]:
        """Generate relevant tags for the email."""
        tags = []

        # Email type tags
        email_type = self._classify_email_type(email)
        tags.append(email_type)

        # Domain-based tags
        domain = email.split('@')[1].lower()
        if self._is_edu_email(domain):
            tags.append('education')
        if self._is_gov_email(domain):
            tags.append('government')

        # Context-based tags (if ML available)
        if context and ML_AVAILABLE:
            # Extract keywords from context
            keywords = self._extract_keywords(context)
            tags.extend(keywords[:5])  # Top 5 keywords

        return list(set(tags))

    def _detect_timezone(self, email: str, company_data: Optional[Dict]) -> Optional[str]:
        """Detect likely timezone based on email and company data."""
        # Simplified timezone detection
        domain = email.split('@')[1].lower()

        # Common domain TLDs to timezones
        tld_timezones = {
            '.uk': 'Europe/London',
            '.de': 'Europe/Berlin',
            '.fr': 'Europe/Paris',
            '.jp': 'Asia/Tokyo',
            '.au': 'Australia/Sydney',
            '.ca': 'America/Toronto',
            '.in': 'Asia/Kolkata'
        }

        for tld, tz in tld_timezones.items():
            if domain.endswith(tld):
                return tz

        # Default to US Eastern for .com
        if domain.endswith('.com'):
            return 'America/New_York'

        return None

    def _check_mx_records(self, domain: str) -> bool:
        """Check if domain has valid MX records."""
        try:
            import dns.resolver
            mx_records = dns.resolver.resolve(domain, 'MX')
            return len(mx_records) > 0
        except:
            # Fallback: assume valid for known providers
            known_providers = {
                'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com'
            }
            return domain in known_providers

    def _has_common_typos(self, email: str) -> bool:
        """Check for common email typos."""
        typos = [
            'gmial.com', 'gmai.com', 'gmil.com',  # Gmail typos
            'yahooo.com', 'yaho.com',  # Yahoo typos
            'outlok.com', 'hotmial.com'  # Outlook typos
        ]
        domain = email.split('@')[1].lower()
        return domain in typos

    def _is_known_provider(self, domain: str) -> bool:
        """Check if domain is a known email provider."""
        known = {
            'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com',
            'icloud.com', 'aol.com', 'protonmail.com', 'mail.com'
        }
        return domain in known

    def _is_personal_email(self, domain: str) -> bool:
        """Check if email is from a personal provider."""
        personal = {
            'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com',
            'icloud.com', 'aol.com'
        }
        return domain in personal

    def _is_edu_email(self, domain: str) -> bool:
        """Check if email is from an educational institution."""
        return '.edu' in domain

    def _is_gov_email(self, domain: str) -> bool:
        """Check if email is from a government domain."""
        return '.gov' in domain

    def _guess_industry(self, domain: str) -> str:
        """Guess industry based on domain."""
        # Simple keyword matching
        industries = {
            'tech': ['tech', 'software', 'app', 'digital', 'cloud'],
            'finance': ['bank', 'finance', 'capital', 'invest', 'fund'],
            'healthcare': ['health', 'medical', 'clinic', 'pharma', 'care'],
            'education': ['edu', 'school', 'academy', 'university'],
            'retail': ['shop', 'store', 'retail', 'commerce'],
            'consulting': ['consult', 'advisory', 'strategy']
        }

        for industry, keywords in industries.items():
            for keyword in keywords:
                if keyword in domain:
                    return industry

        return 'other'

    def _extract_keywords(self, text: str, num_keywords: int = 10) -> List[str]:
        """Extract keywords from text using TF-IDF."""
        if not ML_AVAILABLE:
            return []

        try:
            # Simple keyword extraction
            vectorizer = TfidfVectorizer(
                max_features=num_keywords,
                stop_words='english'
            )
            vectorizer.fit_transform([text])
            keywords = vectorizer.get_feature_names_out()
            return list(keywords)
        except:
            return []

    def _calculate_confidence(self, deliverability: float, risk: float) -> float:
        """Calculate overall confidence score."""
        # Higher deliverability and lower risk = higher confidence
        confidence = (deliverability + (100 - risk)) / 2
        return round(confidence, 2)

    def bulk_analyze(self, emails: List[str], context: str = "") -> List[EmailIntelligence]:
        """Analyze multiple emails in parallel."""
        results = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self.analyze_email, email, context): email
                for email in emails
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    email = futures[future]
                    # Return basic intelligence on error
                    results.append(EmailIntelligence(
                        email=email,
                        is_valid=False,
                        deliverability_score=0,
                        risk_score=100,
                        email_type='unknown',
                        sentiment='neutral',
                        intent='other',
                        priority='low',
                        language='en',
                        timezone=None,
                        social_profiles={},
                        company_data=None,
                        contact_info={'email': email},
                        tags=['error'],
                        metadata={'error': str(e)}
                    ))

        return results