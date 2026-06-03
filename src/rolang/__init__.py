"""RoLang"""

from .parser import parse
from .ast import *
from .resolver import resolve, resolve_with_modules
from .symbols import (
    SymbolTable,
    Symbol,
    SymbolId,
    SymbolKind,
    Namespace,
    Scope,
    ScopeKind,
    ResolutionResult,
    ResolutionError,
    ResolutionErrorKind,
)
from .checker import (
    typecheck,
    TypeCheckResult,
    TypeError,
    TypeErrorKind,
    CalleeId,
    CalleeKind,
)
from .types import (
    TypeId,
    TypeKind,
    TypeTable,
    TypeInfo,
    PrimitiveType,
)
from .members import (
    FieldInfo,
    MethodInfo,
    TypeMembers,
    MemberResolver,
    get_type_members,
)
from .hir import (
    # Base
    HirNode,
    HirProgram,
    # Items
    HirFunction,
    HirExternFunc,
    HirParam,
    HirStruct,
    HirField,
    HirEnum,
    HirEnumCase,
    HirProtocol,
    HirFuncRequirement,
    HirPropRequirement,
    HirExtension,
    # Statements
    HirStmt,
    HirBlock,
    HirVarDecl,
    HirAssign,
    HirExprStmt,
    HirReturn,
    HirBreak,
    HirContinue,
    HirIf,
    HirIfLet,
    HirGuard,
    HirWhile,
    HirFor,
    HirSwitchCase,
    HirSwitch,
    HirDefer,
    # Expressions
    HirExpr,
    HirLiteral,
    HirVar,
    HirBinaryOp,
    HirUnaryOp,
    HirTernary,
    HirCall,
    HirMethodCall,
    HirFieldAccess,
    HirSubscript,
    HirTuple,
    HirArray,
    HirDict,
    HirLambda,
    HirStructInit,
    HirEnumConstruct,
    HirCast,
    HirTypeCheck,
    # Desugared optional operations
    HirOptionalSome,
    HirOptionalNone,
    HirOptionalMatch,
    # Patterns
    HirPattern,
    HirWildcardPattern,
    HirBindingPattern,
    HirLiteralPattern,
    HirTuplePattern,
    HirEnumCasePattern,
    HirOrPattern,
)
from .hir_builder import (
    build_hir,
    HirBuildResult,
    HirBuilder,
)
from .monomorphize import (
    monomorphize,
    MonomorphizationResult,
    Monomorphizer,
    InstanceKey,
    TypeSubstitution,
    FunctionInstance,
    StructInstance,
    EnumInstance,
    mangle_name,
)
from .mir import (
    # ID types
    LocalId,
    BlockId,
    ValueId,
    # Local/Place
    Local,
    Place,
    PlaceProjection,
    FieldProjection,
    IndexProjection,
    DerefProjection,
    # Operands
    Operand,
    CopyOperand,
    MoveOperand,
    ConstantOperand,
    ConstantKind,
    operand_type,
    # Operation kinds
    BinOpKind,
    CmpOpKind,
    UnaryOpKind,
    LogicOpKind,
    # Operations
    Op,
    BinOp,
    CmpOp,
    UnaryOp,
    CastOp,
    MakeStruct,
    MakeEnum,
    MakeSome,
    MakeNone,
    ExtractField,
    ExtractEnumPayload,
    GetTag,
    Assign,
    Store,
    Load,
    Retain,
    Release,
    CallStatic,
    CallVTable,
    CallWitness,
    # Async operations
    Suspend,
    TaskSpawn,
    TaskJoin,
    TaskYield,
    TaskComplete,
    # Terminators
    Terminator,
    Branch,
    CondBranch,
    SwitchInt,
    Return,
    Unreachable,
    # Blocks and Functions
    Block,
    MirFunction,
    MirField,
    MirStruct,
    MirEnumCase,
    MirEnum,
    MirExternFunc,
    MirProgram,
    MirBuildResult,
    # Validation
    validate_function,
    validate_program,
    get_terminator_targets,
    # Pretty printing
    format_operand,
    format_place,
    format_local,
    format_op,
    format_terminator,
    format_block,
    format_function,
    format_program,
)
from .mir_builder import (
    build_mir,
    MirBuilder,
    MirFunctionBuilder,
    LoopContext,
    DeferContext,
)
from .arc_insertion import (
    # Main entry point
    insert_arc,
    # Result type
    ArcInsertionResult,
    # Data structures
    RcState,
    LocalInfo,
    BlockAnalysis,
    OpOwnership,
    # Analysis functions
    collect_ref_locals,
    compute_use_def,
    compute_liveness,
    analyze_op_ownership,
    # Transformation
    insert_arc_ops,
    insert_arc_ops_in_block,
    # Verification
    verify_arc_correctness,
)
from .arc_optimization import (
    # Statistics
    ArcOptStats,
    # Analysis
    UseInfo,
    LocalUseInfo,
    analyze_uses,
    # Optimizer
    ArcOptimizer,
    # Entry points
    optimize_arc,
    optimize_arc_program,
)
from .codegen import (
    # Main entry points
    compile_to_llvm,
    compile_to_object,
    # Result type
    CodegenResult,
    # Components
    TypeLayoutCache,
    RuntimeABI,
    FunctionCodegen,
)
from .diagnostics import (
    # Core types
    Severity,
    SourceLocation,
    Diagnostic,
    # Formatters
    DiagnosticFormatter,
    DiagnosticCollector,
    create_formatter,
)
from .driver import (
    # Options
    EmitKind,
    OptLevel,
    CompileOptions,
    CompileResult,
    # Driver
    CompilationDriver,
    compile_source,
)
from .cli import main as cli_main
from .async_lowering import (
    AsyncFrame,
    AsyncLoweringResult,
    lower_async,
)
from .module import (
    ModuleState,
    Export,
    ExtensionExport,
    Module,
    ModuleExports,
    ModuleGraph,
    module_name_from_path,
)

__version__ = "0.1.0"
