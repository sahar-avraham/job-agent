"""Check which of a posting's technologies actually appear in the tailored CV.

The selection step is a model choosing facts, and nothing until now verified that the
thing the posting asks for is in the document. This does that in plain code: read the
technologies out of the requirements, look for each one in the CV, and sort the answer
into three groups.

- covered: the posting asks for it and the CV says it.
- missed: facts.md has it, but the selection left it out. Worth fixing.
- absent: facts.md does not have it at all. That is a real gap, and it says more about
  whether the job is worth an application than any score does.

Matching is by alias, because the same thing is written a dozen ways: "Node.js", "NodeJS",
"node". Only whole words count, so "C" does not match every word with a c in it.
"""

from __future__ import annotations

import re

# Canonical name, then every spelling a posting might use. Kept to things that are either
# in facts.md or common enough in these postings to be worth reporting.
TECHNOLOGIES = {
    "Java": ["java"],
    "Spring": ["spring", "spring boot", "springboot"],
    "Python": ["python"],
    "C": [r"\bc\b", "c language"],
    "C++": [r"c\+\+", "cpp"],
    "C#": [r"c#", r"\.net", "dotnet"],
    "Go": ["golang", r"\bgo\b"],
    "JavaScript": ["javascript", r"\bjs\b"],
    "TypeScript": ["typescript", r"\bts\b"],
    "Node.js": [r"node\.?js", r"\bnode\b"],
    "React": ["react", "react.js", "reactjs"],
    "Angular": ["angular"],
    "Vue": ["vue", "vue.js"],
    "SQL": ["sql"],
    "MySQL": ["mysql"],
    "PostgreSQL": ["postgres", "postgresql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch", "elastic search", "opensearch"],
    "Kafka": ["kafka"],
    "RabbitMQ": ["rabbitmq"],
    "JMS": ["jms", "activemq", "artemis"],
    "REST": ["rest", "restful", "rest api", "rest apis"],
    "GraphQL": ["graphql"],
    "Microservices": ["microservice", "microservices"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", r"\bk8s\b"],
    "Terraform": ["terraform", "terragrunt"],
    "Ansible": ["ansible"],
    "Jenkins": ["jenkins"],
    "GitHub Actions": ["github actions"],
    "GitLab CI": ["gitlab ci", "gitlab-ci"],
    "CI/CD": [r"ci/cd", r"ci / cd", "continuous integration", "continuous delivery"],
    "Git": ["git", "github", "gitlab", "bitbucket"],
    "Linux": ["linux", "ubuntu", "debian", "centos", "rhel"],
    "Bash": ["bash", "shell scripting", "powershell"],
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud"],
    "VMware": ["vmware", "vsphere", "esxi"],
    "Networking": ["tcp/ip", "networking", "dns", "http", "https", "load balanc"],
    "Security": ["security", "oauth", "jwt", "authentication", "authorization", "encryption"],
    "Testing": ["unit test", "unit tests", "junit", "pytest", "mockito", "testing"],
    "Selenium": ["selenium", "playwright", "cypress", "appium"],
    "Machine Learning": ["machine learning", r"\bml\b", "deep learning", "pytorch", "tensorflow"],
    "LLM": ["llm", "llms", "genai", "gen ai", "generative ai", "openai", "langchain", "rag",
            "claude", "gemini", "copilot", "cursor", "ai coding", "ai-assisted", "ai assisted"],
    "Data pipelines": ["etl", "data pipeline", "data pipelines", "airflow", "spark", "databricks"],
    "Maven": ["maven", "gradle"],
    "Agile": ["agile", "scrum", "kanban"],
}

# Where the demands are. The same markers the years rule uses, kept here so the two stay independent.
REQUIREMENTS = re.compile(
    r"requirements|qualifications|what you bring|what you.ll bring|who you are|about you|you have|you bring"
    r"|what we.re looking for|what we look for|must have|skills|דרישות|מה אנחנו מחפשים", re.I)

PATTERNS = {name: re.compile("|".join(a if a.startswith(("\\", "(")) or "\\" in a else re.escape(a)
                                      for a in aliases), re.I)
            for name, aliases in TECHNOLOGIES.items()}
BOUNDED = re.compile(r"(?<![\w+#.])")


def mentioned(text: str) -> set[str]:
    """Every technology named anywhere in this text."""
    text = text or ""
    found = set()
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            before = text[match.start() - 1] if match.start() else " "
            after = text[match.end()] if match.end() < len(text) else " "
            # Whole words only, so "Go" does not match "Google" and "C" does not match "CI".
            if not (before.isalnum() or before in "+#") and not (after.isalnum() or after in "+#"):
                found.add(name)
                break
    return found


def demanded(description: str) -> set[str]:
    """What the posting asks for, read from its requirements when it marks them."""
    section = REQUIREMENTS.search(description or "")
    if section:
        # Some postings mark a requirements section but name their stack above it.
        return mentioned(description[section.start():]) or mentioned(description)
    return mentioned(description)


# Naming one of these is naming the general thing, so a CV that says GitHub Actions covers CI/CD.
SATISFIED_BY = {
    "CI/CD": {"GitHub Actions", "GitLab CI", "Jenkins"},
    "REST": {"Spring"},
    "JavaScript": {"React", "Angular", "Vue", "Node.js", "TypeScript"},
    "Testing": {"Testing"},
    "Microservices": set(),
}


def widen(found: set[str]) -> set[str]:
    return found | {general for general, specifics in SATISFIED_BY.items() if found & specifics}


def coverage(description: str, document: str, facts_text: str) -> dict[str, list[str]]:
    """Sort the posting's technologies into covered, missed by the selection, and absent."""
    asked = demanded(description)
    in_cv, in_facts = widen(mentioned(document)), widen(mentioned(facts_text))
    return {
        "covered": sorted(asked & in_cv),
        "missed": sorted(asked & in_facts - in_cv),
        "absent": sorted(asked - in_facts - in_cv),
    }


def summary(result: dict[str, list[str]]) -> str:
    parts = [f"{len(result['covered'])} of {sum(len(v) for v in result.values())} asked for are in the CV"]
    if result["missed"]:
        parts.append("left out though facts.md has it: " + ", ".join(result["missed"]))
    if result["absent"]:
        parts.append("not in facts.md: " + ", ".join(result["absent"]))
    return " | ".join(parts)
