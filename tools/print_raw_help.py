#!/usr/bin/env python

import sys

import pdftl

pdftl.registry_init.initialize_registry()
for topic_arg in sys.argv[1:]:
    hi = "=" * 10
    topic = pdftl.cli.main._find_help_command([topic_arg])
    tag = f"RAW HELP FOR ARG: '{topic_arg}' => TOPIC: '{topic}'"
    print(hi + f"  <{tag}>  " + hi)
    pdftl.cli.help.print_help(topic, raw=True)
    print(hi + f"  </{tag}>  " + hi)
