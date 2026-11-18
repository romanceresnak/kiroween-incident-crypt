"""polish TASK-07 (REQ-IAM-01): the learning IAM policy is exactly the two approved permissions.

This reads the Terraform source; it cannot show what AWS enforces. The plan and the IAM
simulator checks in .kiro/specs/polish/tasks.md do that.
"""

from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).parents[2] / "infra"
MODULE = (INFRA / "modules" / "pod_identity" / "main.tf").read_text()
VARIABLES = (INFRA / "modules" / "pod_identity" / "variables.tf").read_text()
ROOT = (INFRA / "main.tf").read_text()


def block(source: str, header: str) -> str:
    start = source.index(header)
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        depth += source[j] == "{"
        depth -= source[j] == "}"
        if depth == 0:
            return source[start:j + 1]
    raise AssertionError(f"unterminated block {header}")


def statements(policy: str) -> list[dict[str, list[str] | str]]:
    out = []
    for stmt in re.findall(r"statement \{.*?\n  \}", policy, re.S):
        out.append({
            "sid": re.search(r'sid\s*=\s*"([^"]+)"', stmt).group(1),
            "effect": re.search(r'effect\s*=\s*"([^"]+)"', stmt).group(1),
            "actions": re.findall(r'"([a-z0-9]+:[A-Za-z*]+)"',
                                  re.search(r"actions\s*=\s*\[(.*?)\]", stmt, re.S).group(1)),
            "resources": re.findall(r'"([^"]+)"|(var\.[a-z_]+)',
                                    re.search(r"resources\s*=\s*\[(.*?)\]", stmt, re.S).group(1)),
        })
    return out


POLICY = block(MODULE, 'data "aws_iam_policy_document" "learn"')


def test_exactly_two_allow_statements() -> None:
    found = statements(POLICY)
    assert [(s["sid"], s["effect"]) for s in found] == [
        ("WriteLearnedPostmortems", "Allow"), ("StartKnowledgeBaseIngestion", "Allow")]


def test_the_only_actions_are_put_object_and_start_ingestion_job() -> None:
    found = {s["sid"]: s["actions"] for s in statements(POLICY)}
    assert found == {"WriteLearnedPostmortems": ["s3:PutObject"],
                     "StartKnowledgeBaseIngestion": ["bedrock:StartIngestionJob"]}
    assert "*" not in re.sub(r"/learned/\*", "", POLICY.split("resources")[0])


def test_the_resources_are_the_learned_prefix_and_the_knowledge_base_only() -> None:
    write, ingest = statements(POLICY)
    assert [a or b for a, b in write["resources"]] == [
        "arn:aws:s3:::${var.postmortem_bucket_name}/learned/*"]
    assert [a or b for a, b in ingest["resources"]] == ["var.knowledge_base_arn"]


def test_only_the_orchestrator_gets_it_and_it_is_an_inline_role_policy() -> None:
    assert re.search(r'variable "learning_agents".*?default\s*=\s*\["orchestrator"\]',
                     VARIABLES, re.S)
    resource = block(MODULE, 'resource "aws_iam_role_policy" "learn"')
    assert "for_each = local.learning_agents" in resource
    assert 'name   = "learn-postmortems"' in resource
    assert "aws_iam_role.agent[each.key].id" in resource
    assert "learning_agents = toset([for a in var.agent_names" in MODULE


def test_the_root_module_passes_the_bucket_from_the_knowledge_base_module() -> None:
    wiring = block(ROOT, 'module "pod_identity"')
    assert "postmortem_bucket_name = module.bedrock_kb.postmortem_bucket_name" in wiring
    # No output, resource or variable outside IAM was added for this.
    assert 'output "knowledge_base_data_source_id"' not in ROOT
