# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/completion/grammar.py

import re
import textwrap

from pdftl.cli.constants import SUB_EACH, SUB_END, SUB_START
from pdftl.core.registry import registry
from pdftl.registry_init import initialize_registry

# ============================== IMPORTANT ====================================
# bump this if grammar output changes, both here and in src/pdf/cli/complete.py
GRAMMAR_VERSION = "8"
# =============================================================================


class GrammarBuilder:
    HELP_TOPICS = [
        "help",
        "sign",
        "filter",
        "input",
        "pages",
        "output",
        "completion",
        "examples",
        "pipeline",
        "all",
    ]

    def _sanitize(self, name: str) -> tuple[str, str]:
        """Returns the clean name and regex-safe variable name."""
        clean = name.split()[0]
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", clean)
        return clean, safe

    def _build_operation_rules(self, grammar: list, ops_rules: list, source_ops_rules: list):
        for name, op_obj in registry.operations.items():
            clean_name, safe_name = self._sanitize(name)
            rule_name = f"op_{safe_name}"
            is_source = getattr(op_obj, "type", None) == "source operation"

            if is_source:
                source_ops_rules.append(rule_name)
            else:
                ops_rules.append(rule_name)

            kw_term = f"KW_{safe_name.upper()}"
            grammar.append(f'{kw_term}.5: "{clean_name}"')

            spec = getattr(op_obj, "grammar_spec", None)
            if spec:
                grammar.append(f"{rule_name}: {kw_term} [{spec}]")
            else:
                grammar.append(f"{rule_name}: {kw_term}")

    def _build_option_rules(self, grammar: list, opts_rules: list):
        for name in registry.options:
            clean_name, safe_name = self._sanitize(name)
            rule_name = f"opt_{safe_name}"
            opts_rules.append(rule_name)

            kw_term = f"KW_{safe_name.upper()}"
            grammar.append(f'{kw_term}.5: "{clean_name}"')

            if "<" in name:
                grammar.append(f"{rule_name}: {kw_term} FILE_PATH")
            else:
                grammar.append(f"{rule_name}: {kw_term}")

    def _build_disjunctions(
        self, grammar: list, ops_rules: list, source_ops_rules: list, opts_rules: list
    ):
        # DISJUNCTIONS: Use '!' to keep these rules visible to the completion engine
        if ops_rules:
            grammar.append(f"!operation: {' | '.join(ops_rules)}")
        else:
            grammar.append('!operation: "noop"')

        if source_ops_rules:
            grammar.append(f"!source_operation: {' | '.join(source_ops_rules)}")
        else:
            grammar.append('!source_operation: "noop"')

        if opts_rules:
            grammar.append(f"!option: {' | '.join(opts_rules)}")
        else:
            grammar.append('!option: "noop"')

    def build(self):
        initialize_registry()
        help_topics_str = " | ".join(f'"{t}"' for t in self.HELP_TOPICS)
        grammar = [
            textwrap.dedent(
                f"""
            start: global_flag* cmd_args_section [opt_section]
                 | global_flag* help_cmd [help_topic]

            help_cmd: HELP_KW | HELP_FLAG
            help_topic: HELP_SUB_KW | operation | CHAIN_SEP
            global_flag: comp_cmd | VERSION_FLAG | DEBUG_FLAG
            !comp_cmd: COMP_FLAG (COMP_BASH | COMP_ZSH | COMP_PWSH)

            COMP_FLAG.10: "--completion"
            COMP_BASH: "bash"
            COMP_ZSH: "zsh"
            COMP_PWSH: "powershell"

            HELP_KW.10: "help"
            HELP_FLAG.10: "--help"
            VERSION_FLAG.10: "--version"
            DEBUG_FLAG.10: "--debug"
            HELP_SUB_KW.10: {help_topics_str}

            # --- PIPELINE STRUCTURE ---

            # Recursive definition for inline pipelines
            inline_pipeline: KW_SUB pipeline_body KW_END
            each_pipeline: KW_EACH pipeline_body KW_END
            pipeline_body: input_section [op_section] [opt_section]

            cmd_args_section: input_section [op_section]
                            | source_operation [op_section]
            input_section: file_ref+
            op_section: (operation | CHAIN_SEP)+
            opt_section: option+ global_flag*

            CHAIN_SEP.10: "---"

            # Modified file_ref to accept recursive pipelines
            file_ref: handle_prefix? (PDF_PATH | inline_pipeline | each_pipeline)
            handle_prefix: /[A-Z]=/

            KW_SUB.10: "{SUB_START}"
            KW_EACH.10: "{SUB_EACH}"
            KW_END.10: "{SUB_END}"

            # Use a negative lookahead to prevent PDF_PATH from matching flags starting with --
            # Also exclude \\x01 (the cursor marker)
            # PDF_PATH matches filenames OR handles (e.g. "X", "A")
            PDF_PATH.0: /(?!--)[^ \\t\\n\\x01\\x00-\\x1f]+/
            FILE_PATH.0: /[^ \\t\\n\\x01\\x00-\\x1f]+/

            range_expr: /[0-9]+(-([0-9]+|end))?[A-Z]?/
            cat_spec: (/[A-Z]/ | range_expr)*
            rotate_spec: range_expr? (DIR_ABS | DIR_REL)?
            DIR_ABS: "north"|"south"|"east"|"west"
            DIR_REL: "left"|"right"|"down"

            %import common.WS
            %ignore WS
            """
            )
        ]

        ops_rules = []
        source_ops_rules = []
        opts_rules = []

        self._build_operation_rules(grammar, ops_rules, source_ops_rules)
        self._build_option_rules(grammar, opts_rules)
        self._build_disjunctions(grammar, ops_rules, source_ops_rules, opts_rules)

        return "\n".join(grammar)
