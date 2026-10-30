import ast
from decimal import Decimal, InvalidOperation, localcontext

from app.tools.errors import ToolError


MAX_ABS_VALUE = Decimal("1e12")
MAX_NODES = 50
MAX_EXPRESSION_LENGTH = 200


def calculate(expression: str) -> dict[str, str]:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > MAX_EXPRESSION_LENGTH:
        raise ToolError("Provide a nonempty arithmetic expression of at most 200 characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ToolError("The arithmetic expression is invalid.") from error
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ToolError("The arithmetic expression is too complex.")

    def bounded(value: Decimal) -> Decimal:
        if not value.is_finite() or abs(value) > MAX_ABS_VALUE:
            raise ToolError("The calculation result is too large.")
        return value

    def evaluate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return bounded(Decimal(str(node.value)))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return bounded(value if isinstance(node.op, ast.UAdd) else -value)
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return bounded(left + right)
            if isinstance(node.op, ast.Sub):
                return bounded(left - right)
            if isinstance(node.op, ast.Mult):
                return bounded(left * right)
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ToolError("Division by zero is not allowed.")
                return bounded(left / right)
            if isinstance(node.op, ast.Mod):
                if right == 0:
                    raise ToolError("Division by zero is not allowed.")
                return bounded(left % right)
            if isinstance(node.op, ast.Pow):
                if right != right.to_integral_value() or abs(right) > 10:
                    raise ToolError("Exponents must be whole numbers between -10 and 10.")
                if left == 0 and right < 0:
                    raise ToolError("Division by zero is not allowed.")
                return bounded(left ** int(right))
        raise ToolError("Only numbers, parentheses, +, -, *, /, %, and ** are allowed.")

    try:
        with localcontext() as context:
            context.prec = 28
            result = evaluate(tree)
    except (InvalidOperation, OverflowError) as error:
        raise ToolError("The calculation could not be completed.") from error
    return {"expression": expression, "result": format(result.normalize(), "f")}
