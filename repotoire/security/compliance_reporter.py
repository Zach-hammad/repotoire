"""Compliance report generator for security standards.

Generates compliance reports for common security frameworks:
    - SOC 2 (Service Organization Control 2)
    - ISO 27001 (Information Security Management)
    - PCI DSS (Payment Card Industry Data Security Standard)
    - NIST CSF (Cybersecurity Framework)
    - CIS Controls

Maps security findings to compliance requirements and generates
audit-ready reports.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

from repotoire.models import Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class ComplianceFramework(str, Enum):
    """Supported compliance frameworks."""
    SOC2 = "soc2"
    ISO27001 = "iso27001"
    PCI_DSS = "pci_dss"
    NIST_CSF = "nist_csf"
    CIS = "cis"


class ComplianceReporter:
    """Generate compliance reports from security findings.

    Maps findings to compliance framework requirements and generates
    audit-ready reports.

    Configuration:
        framework: Compliance framework to report against
        findings: List of security findings to analyze
        repository_path: Path to repository
    """

    # Compliance control mappings
    CONTROL_MAPPINGS = {
        ComplianceFramework.SOC2: {
            "CC6.1": {
                "title": "Logical and Physical Access Controls",
                "keywords": ["authentication", "authorization", "access control", "credential"],
            },
            "CC6.6": {
                "title": "Vulnerability Management",
                "keywords": ["vulnerability", "cve", "dependency", "outdated"],
            },
            "CC6.7": {
                "title": "System Security",
                "keywords": ["security", "injection", "xss", "sql", "csrf"],
            },
            "CC7.2": {
                "title": "System Monitoring",
                "keywords": ["logging", "monitoring", "audit trail"],
            },
        },
        ComplianceFramework.ISO27001: {
            "A.9.2.1": {
                "title": "User Registration and De-registration",
                "keywords": ["user", "registration", "authentication"],
            },
            "A.12.6.1": {
                "title": "Management of Technical Vulnerabilities",
                "keywords": ["vulnerability", "patch", "update", "cve"],
            },
            "A.14.2.1": {
                "title": "Secure Development Policy",
                "keywords": ["security", "injection", "validation", "sanitization"],
            },
        },
        ComplianceFramework.PCI_DSS: {
            "6.2": {
                "title": "Security Vulnerabilities",
                "keywords": ["vulnerability", "cve", "security", "patch"],
            },
            "6.5.1": {
                "title": "Injection Flaws",
                "keywords": ["sql injection", "command injection", "ldap injection"],
            },
            "6.5.7": {
                "title": "Cross-Site Scripting (XSS)",
                "keywords": ["xss", "cross-site scripting", "reflected xss"],
            },
            "8.2": {
                "title": "User Authentication",
                "keywords": ["authentication", "password", "credential", "token"],
            },
        },
    }

    def __init__(
        self,
        framework: ComplianceFramework,
        findings: List[Finding],
        repository_path: Optional[Path] = None,
    ):
        """Initialize compliance reporter.

        Args:
            framework: Compliance framework to report against
            findings: List of security findings
            repository_path: Optional path to repository
        """
        self.framework = framework
        self.findings = findings
        self.repository_path = repository_path or Path(".")

    def generate_report(self, output_path: Optional[Path] = None) -> Dict[str, Any]:
        """Generate compliance report.

        Args:
            output_path: Optional path to write JSON report

        Returns:
            Compliance report dictionary
        """
        logger.info(f"Generating {self.framework.value} compliance report")

        report = {
            "framework": self.framework.value,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "repository": str(self.repository_path),
            "summary": self._generate_summary(),
            "controls": self._map_findings_to_controls(),
            "recommendations": self._generate_recommendations(),
        }

        if output_path:
            self._write_report(report, output_path)

        return report

    def _generate_summary(self) -> Dict[str, Any]:
        """Generate summary statistics.

        Returns:
            Summary dictionary
        """
        # Count findings by severity
        severity_counts = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        }

        for finding in self.findings:
            severity = finding.severity.value.lower()
            if severity in severity_counts:
                severity_counts[severity] += 1

        # Calculate compliance score (simplified)
        total_findings = len(self.findings)
        critical_high = severity_counts["critical"] + severity_counts["high"]

        if total_findings == 0:
            compliance_score = 100
        else:
            # Penalize critical/high more heavily
            penalty = (critical_high * 10) + (severity_counts["medium"] * 3) + severity_counts["low"]
            compliance_score = max(0, 100 - penalty)

        return {
            "total_findings": total_findings,
            "by_severity": severity_counts,
            "compliance_score": compliance_score,
            "status": self._get_compliance_status(compliance_score),
        }

    def _get_compliance_status(self, score: int) -> str:
        """Get compliance status from score.

        Args:
            score: Compliance score (0-100)

        Returns:
            Status string
        """
        if score >= 90:
            return "compliant"
        elif score >= 70:
            return "mostly_compliant"
        elif score >= 50:
            return "partially_compliant"
        else:
            return "non_compliant"

    def _map_findings_to_controls(self) -> List[Dict[str, Any]]:
        """Map findings to compliance controls.

        Returns:
            List of controls with mapped findings
        """
        controls = []
        control_mappings = self.CONTROL_MAPPINGS.get(self.framework, {})

        for control_id, control_info in control_mappings.items():
            mapped_findings = []

            for finding in self.findings:
                if self._finding_matches_control(finding, control_info):
                    mapped_findings.append({
                        "id": finding.id,
                        "title": finding.title,
                        "severity": finding.severity.value,
                        "detector": finding.detector,
                        "affected_files": finding.affected_files[:5],  # Limit to 5
                    })

            controls.append({
                "control_id": control_id,
                "title": control_info["title"],
                "findings_count": len(mapped_findings),
                "status": "pass" if len(mapped_findings) == 0 else "fail",
                "findings": mapped_findings,
            })

        return controls

    def _finding_matches_control(
        self, finding: Finding, control_info: Dict[str, Any]
    ) -> bool:
        """Check if finding matches a control.

        Args:
            finding: Security finding
            control_info: Control information with keywords

        Returns:
            True if finding matches control
        """
        keywords = control_info.get("keywords", [])

        # Check title and description
        text = f"{finding.title} {finding.description}".lower()

        for keyword in keywords:
            if keyword.lower() in text:
                return True

        return False

    def _generate_recommendations(self) -> List[str]:
        """Generate remediation recommendations.

        Returns:
            List of recommendation strings
        """
        recommendations = []

        summary = self._generate_summary()
        severity_counts = summary["by_severity"]

        if severity_counts["critical"] > 0:
            recommendations.append(
                f"URGENT: Address {severity_counts['critical']} critical security "
                "findings immediately. These pose significant compliance risks."
            )

        if severity_counts["high"] > 0:
            recommendations.append(
                f"HIGH PRIORITY: Remediate {severity_counts['high']} high-severity "
                "findings within 30 days to maintain compliance."
            )

        if severity_counts["medium"] > 0:
            recommendations.append(
                f"MEDIUM PRIORITY: Plan fixes for {severity_counts['medium']} "
                "medium-severity findings within 90 days."
            )

        # Framework-specific recommendations
        if self.framework == ComplianceFramework.SOC2:
            recommendations.append(
                "Implement continuous monitoring and automated vulnerability scanning "
                "to maintain SOC 2 compliance."
            )
        elif self.framework == ComplianceFramework.PCI_DSS:
            recommendations.append(
                "Conduct quarterly vulnerability scans and annual penetration testing "
                "as required by PCI DSS."
            )

        return recommendations

    def _write_report(self, report: Dict[str, Any], output_path: Path) -> None:
        """Write report to file.

        Args:
            report: Report dictionary
            output_path: Output file path
        """
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w") as f:
                json.dump(report, f, indent=2)

            logger.info(f"Compliance report written to: {output_path}")

        except Exception as e:
            logger.error(f"Failed to write report: {e}")
            raise

    def generate_markdown_report(self, output_path: Optional[Path] = None) -> str:
        """Generate human-readable Markdown report.

        Args:
            output_path: Optional path to write markdown file

        Returns:
            Markdown report string
        """
        report = self.generate_report()

        md = f"""# {self.framework.value.upper()} Compliance Report

**Generated:** {report['generated_at']}
**Repository:** {report['repository']}

## Summary

- **Compliance Score:** {report['summary']['compliance_score']}/100
- **Status:** {report['summary']['status'].replace('_', ' ').title()}
- **Total Findings:** {report['summary']['total_findings']}

### Findings by Severity

- **Critical:** {report['summary']['by_severity']['critical']}
- **High:** {report['summary']['by_severity']['high']}
- **Medium:** {report['summary']['by_severity']['medium']}
- **Low:** {report['summary']['by_severity']['low']}

## Controls Assessment

"""

        for control in report["controls"]:
            status_icon = "✅" if control["status"] == "pass" else "❌"
            md += f"### {status_icon} {control['control_id']}: {control['title']}\n\n"
            md += f"**Status:** {control['status'].upper()}  \n"
            md += f"**Findings:** {control['findings_count']}\n\n"

            if control["findings"]:
                md += "**Issues:**\n\n"
                for finding in control["findings"][:3]:  # Show first 3
                    md += f"- [{finding['severity'].upper()}] {finding['title']}\n"

                if control["findings_count"] > 3:
                    md += f"- ... and {control['findings_count'] - 3} more\n"

                md += "\n"

        md += "## Recommendations\n\n"
        for i, rec in enumerate(report["recommendations"], 1):
            md += f"{i}. {rec}\n\n"

        if output_path:
            output_path.write_text(md)
            logger.info(f"Markdown report written to: {output_path}")

        return md
