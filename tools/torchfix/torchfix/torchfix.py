from pathlib import Path
import yaml
import libcst as cst
import libcst.codemod as codemod
import libcst.matchers as m
from typing import Optional, List, Tuple

from .common import LintViolation, deep_multi_replace

__version__ = "0.0.2"


class TorchVisitor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (
        cst.metadata.QualifiedNameProvider,
        cst.metadata.WhitespaceInclusivePositionProvider,
    )

    def _call_replacement(self, node: cst.Call, qualified_name) -> cst.Call:
        def call_with_name_changes(
            node: cst.Call, old_qualified_name: str, new_qualified_name: str
        ) -> cst.Call:
            old_begin, _, old_last = old_qualified_name.rpartition(".")
            new_begin, _, new_last = new_qualified_name.rpartition(".")

            # If the only difference is the last name part.
            if old_begin == new_begin:
                replacement = node.with_deep_changes(
                    old_node=node.func.attr,
                    value=new_last,
                )

            # If the the last name part is the same and
            # originally called without a dot: don't change the call site,
            # just change the imports elsewhere.
            elif old_last == new_last and isinstance(node.func, cst.Name):
                replacement = None

            # Replace with new_qualified_name.
            else:
                replacement = node.with_changes(
                    func=cst.parse_expression(new_qualified_name)
                )
            return replacement

        # `torch.range` documented signature is not a valid Python signature,
        # so it's hard to generalize this.
        def _get_range_args(node: cst.Call) -> Tuple[cst.Arg, Optional[cst.Arg]]:
            "Return (`end`, `step`) from a `range` call"
            end_arg = None
            step_arg = None
            non_kw_args = []
            for arg in node.args:
                if arg.keyword is None:
                    non_kw_args.append(arg)
                else:
                    if arg.keyword.value == "end":
                        end_arg = arg
                    elif arg.keyword.value == "step":
                        step_arg = arg

            if end_arg is None:
                if len(non_kw_args) == 1:
                    end_arg = non_kw_args[0]
                elif len(non_kw_args) == 2:
                    end_arg = non_kw_args[1]
                elif len(non_kw_args) == 3:
                    end_arg = non_kw_args[1]
                    step_arg = non_kw_args[2]

            return end_arg, step_arg

        replacement = None

        # Replace names for functions that have drop-in replacement.
        function_name_replacement = self.deprecated_config.get(qualified_name, {}).get(
            "replacement", ""
        )
        if function_name_replacement:
            replacement = call_with_name_changes(
                node, qualified_name, function_name_replacement
            )

        # Replace `range` with `arange`.
        # Add `step` to the `end` argument as `arange` has the interval `[start, end)`.
        if qualified_name == "torch.range":
            end_arg, step_arg = _get_range_args(node)
            step = 1
            if step_arg is not None:
                # `step` is a literal integer
                if isinstance(step_arg.value, cst.Integer):
                    step = int(step_arg.value.value)

                # `step` is unary minus and an integer (i.e. negative integer)
                elif m.matches(
                    step_arg,
                    m.Arg(
                        value=m.UnaryOperation(
                            operator=m.Minus(), expression=m.Integer()
                        )
                    ),
                ):
                    step = -int(step_arg.value.expression.value)

                # Bail out, don't know how to update with non-integer `step`.
                else:
                    return None

            updated_end_arg = None

            # `end` is a literal (positive) integer
            if isinstance(end_arg.value, cst.Integer):
                end = int(end_arg.value.value) + step
                if end >= 0:
                    updated_end_arg = end_arg.with_deep_changes(
                        old_node=end_arg.value, value=str(end)
                    )
                else:
                    # `end` became negative
                    updated_end_arg = end_arg.with_changes(
                        value=cst.UnaryOperation(
                            operator=cst.Minus(),
                            expression=cst.Integer(value=str(-end)),
                        )
                    )

            # `end` is a unary minus and an integer (i.e. negative integer)
            elif m.matches(
                end_arg,
                m.Arg(
                    value=m.UnaryOperation(operator=m.Minus(), expression=m.Integer())
                ),
            ):
                end = -int(end_arg.value.expression.value) + step
                if end < 0:
                    updated_end_arg = end_arg.with_deep_changes(
                        old_node=end_arg.value.expression, value=str(-end)
                    )
                else:
                    # `end` became non-negative
                    updated_end_arg = end_arg.with_changes(
                        value=cst.Integer(value=str(end))
                    )

            # `end` is an expression with `- 1` at the end: remove the `- 1`.
            # This is a common occurrence, thus special handling.
            elif m.matches(
                end_arg,
                m.Arg(
                    value=m.BinaryOperation(
                        operator=m.Subtract(), right=m.Integer(value="1")
                    )
                ),
            ):
                updated_end_arg = end_arg.with_changes(value=end_arg.value.left)

            # `end` something else: add `+ 1` at the end
            else:
                updated_end_arg = end_arg.with_changes(
                    value=cst.BinaryOperation(
                        left=end_arg.value,
                        operator=cst.Add(),
                        right=cst.Integer(value="1"),
                    )
                )

            replacement = node
            if updated_end_arg is not None:
                replacement = replacement.deep_replace(end_arg, updated_end_arg)
            replacement = replacement.with_deep_changes(
                old_node=replacement.func.attr, value="arange"
            )

        return replacement

    def __init__(self, deprecated_config=None):
        self.deprecated_config = {} if deprecated_config is None else deprecated_config
        self.violations: List[LintViolation] = []

    def visit_Call(self, node):
        # Guard against situations like `vmap(a)(b)`:
        #
        # Call(
        #   func=Call(
        #       func=Name(
        #         value='vmap',
        #
        # The QualifiedName metadata for the outer call will be the same
        # as for the inner call.
        if isinstance(node.func, cst.Call):
            return

        name_metadata = list(
            self.get_metadata(cst.metadata.QualifiedNameProvider, node)
        )
        if not name_metadata:
            return
        qualified_name = name_metadata[0].name

        if qualified_name in self.deprecated_config:
            position_metadata = self.get_metadata(
                cst.metadata.WhitespaceInclusivePositionProvider, node
            )
            if self.deprecated_config[qualified_name]["remove_pr"] is None:
                error_code = "TOR101"
                message = f"Use of deprecated function {qualified_name}"
            else:
                error_code = "TOR001"
                message = f"Use of removed function {qualified_name}"
            replacement = self._call_replacement(node, qualified_name)

            self.violations.append(
                LintViolation(
                    error_code=error_code,
                    message=message,
                    line=position_metadata.start.line,
                    column=position_metadata.start.column,
                    node=node,
                    replacement=replacement,
                )
            )


# TODO: refactor/generalize this.
class _UpdateFunctorchImports(cst.CSTTransformer):
    REPLACEMENTS = {
        "vmap",
        "grad",
        "vjp",
        "jvp",
        "jacrev",
        "jacfwd",
        "hessian",
        "functionalize",
    }

    def __init__(self):
        self.changed = False

    def leave_ImportFrom(
        self, node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if node.module.value == "functorch" and all(
            name.name.value in self.REPLACEMENTS for name in node.names
        ):
            self.changed = True
            return updated_node.with_changes(module=cst.parse_expression("torch.func"))
        return updated_node


def _read_deprecated_config(path=None):
    if path is None:
        path = Path(__file__).absolute().parent / "deprecated_symbols.yaml"

    deprecated_config = {}
    with open(path) as f:
        for item in yaml.load(f, yaml.SafeLoader):
            deprecated_config[item["name"]] = item
    return deprecated_config


class TorchChecker:
    name = "TorchFix"
    version = __version__

    # The parameters need to have these exact names.
    # See https://flake8.pycqa.org/en/latest/plugin-development/plugin-parameters.html
    # `tree` is unused, but the plugin doesn't work without it.
    def __init__(self, tree, lines):
        module = cst.parse_module("".join(lines))
        visitor = TorchVisitor(_read_deprecated_config())
        module = cst.MetadataWrapper(module, unsafe_skip_copy=True)
        module.visit(visitor)
        self.violations = visitor.violations

    def run(self):
        for violation in self.violations:
            yield violation.flake8_result()


class TorchCodemod(codemod.Codemod):
    def transform_module_impl(self, module: cst.Module) -> cst.Module:
        visitor = TorchVisitor(_read_deprecated_config())
        fixes_count = 0

        # We use `unsafe_skip_copy`` here not only to save some time, but
        # because `deep_replace`` is identity-based and will not work on
        # the original module if the wrapper does a deep copy:
        # in that case we would need to use `wrapped_module.module`
        # instead of `module`.
        wrapped_module = cst.MetadataWrapper(module, unsafe_skip_copy=True)
        wrapped_module.visit(visitor)

        violations = visitor.violations
        replacement_map = {}
        for violation in violations:
            if violation.replacement is not None:
                replacement_map[id(violation.node)] = violation.replacement
                fixes_count += 1
            try:
                path = Path(self.context.filename).relative_to(Path.cwd())
            except ValueError:
                # Not a subpath of a current dir, use absolute path
                path = self.context.filename
            print(f"{path}{violation.codemod_result()}")

        new_module = deep_multi_replace(module, replacement_map)

        update_imports_visitor = _UpdateFunctorchImports()
        new_module = new_module.visit(update_imports_visitor)

        if fixes_count == 0 and not update_imports_visitor.changed:
            raise codemod.SkipFile("No changes")

        return new_module