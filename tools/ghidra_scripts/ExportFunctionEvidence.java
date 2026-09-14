// Export bounded, machine-readable evidence for one function.
// @category Autodecomp

import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Set;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.decompiler.signature.SignatureResult;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.block.BasicBlockModel;
import ghidra.program.model.block.CodeBlock;
import ghidra.program.model.block.CodeBlockIterator;
import ghidra.program.model.block.CodeBlockReference;
import ghidra.program.model.block.CodeBlockReferenceIterator;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.listing.Parameter;
import ghidra.program.model.listing.Variable;
import ghidra.program.model.pcode.HighFunction;
import ghidra.program.model.pcode.PcodeBlockBasic;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.PcodeOpAST;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class ExportFunctionEvidence extends GhidraScript {
    private static final int MAX_HIGH_PCODE_OPS = 10_000;
    private static final int BSIM_SIGNATURE_SETTINGS = 0x4d;

    private static final class NormalizedIr {
        boolean completed;
        String error = "";
        boolean pcodeTruncated;
        int operationCount;
        final StringBuilder operations = new StringBuilder();
        final StringBuilder blocks = new StringBuilder();
    }

    private static final class BsimSignature {
        boolean completed;
        String error = "";
        final StringBuilder features = new StringBuilder();
        int featureCount;
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2 || args.length > 3) {
            throw new IllegalArgumentException(
                "usage: ExportFunctionEvidence.java ADDRESS OUTPUT_JSON [DECOMPILE_TIMEOUT_SECONDS]");
        }

        String requestedAddress = args[0];
        Path outputPath = Path.of(args[1]).toAbsolutePath().normalize();
        int decompileTimeout = args.length == 3 ? Integer.parseInt(args[2]) : 30;
        if (decompileTimeout < 1 || decompileTimeout > 120) {
            throw new IllegalArgumentException("decompile timeout must be between 1 and 120 seconds");
        }

        Address address = toAddr(requestedAddress);
        FunctionManager functionManager = currentProgram.getFunctionManager();
        Function function = functionManager.getFunctionAt(address);
        if (function == null) {
            function = functionManager.getFunctionContaining(address);
        }
        if (function == null) {
            throw new IllegalStateException("Ghidra found no function at " + requestedAddress);
        }

        Listing listing = currentProgram.getListing();
        List<String> calls = new ArrayList<>();
        List<String> callers = new ArrayList<>();
        List<String> dataRefs = new ArrayList<>();
        StringBuilder instructionsJson = new StringBuilder();
        int instructionCount = appendInstructions(
            instructionsJson, listing, functionManager, function, calls, dataRefs);
        appendCallers(callers, functionManager, function);

        StringBuilder blocksJson = new StringBuilder();
        int blockCount = appendBlocks(blocksJson, function);

        StringBuilder parametersJson = new StringBuilder();
        appendParameters(parametersJson, function.getParameters());
        StringBuilder localsJson = new StringBuilder();
        appendLocals(localsJson, function.getLocalVariables());

        DecompInterface decompiler = new DecompInterface();
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        decompiler.setSimplificationStyle("decompile");
        boolean opened = decompiler.openProgram(currentProgram);
        DecompileResults decompileResults = opened
            ? decompiler.decompileFunction(function, decompileTimeout, monitor)
            : null;
        boolean decompiled = decompileResults != null && decompileResults.decompileCompleted();
        String decompileError = decompileResults == null
            ? "failed to open program in decompiler"
            : decompileResults.getErrorMessage();
        String decompiledC = decompiled && decompileResults.getDecompiledFunction() != null
            ? decompileResults.getDecompiledFunction().getC()
            : "";
        decompiler.dispose();

        NormalizedIr normalizedIr = decompileNormalized(function, decompileTimeout);
        BsimSignature bsimSignature = generateBsimSignature(function, decompileTimeout);

        StringBuilder json = new StringBuilder(32_768);
        json.append("{\n");
        json.append("  \"schema_version\": 2,\n");
        json.append("  \"program\": {");
        json.append("\"name\": ").append(q(currentProgram.getName())).append(", ");
        json.append("\"language\": ").append(q(currentProgram.getLanguageID().toString())).append(", ");
        json.append("\"compiler_spec\": ")
            .append(q(currentProgram.getCompilerSpec().getCompilerSpecID().toString())).append(", ");
        json.append("\"image_base\": ").append(q(currentProgram.getImageBase().toString()));
        json.append("},\n");
        json.append("  \"function\": {");
        json.append("\"requested_address\": ").append(q(requestedAddress)).append(", ");
        json.append("\"entry\": ").append(q(function.getEntryPoint().toString())).append(", ");
        json.append("\"end\": ").append(q(function.getBody().getMaxAddress().toString())).append(", ");
        json.append("\"ghidra_name\": ").append(q(function.getName())).append(", ");
        json.append("\"signature\": ").append(q(function.getPrototypeString(false, true))).append(", ");
        json.append("\"calling_convention\": ").append(q(function.getCallingConventionName())).append(", ");
        json.append("\"return_type\": ").append(q(function.getReturnType().getDisplayName())).append(", ");
        json.append("\"parameter_count\": ").append(function.getParameterCount()).append(", ");
        json.append("\"is_thunk\": ").append(function.isThunk()).append(", ");
        json.append("\"instruction_count\": ").append(instructionCount).append(", ");
        json.append("\"basic_block_count\": ").append(blockCount);
        json.append("},\n");
        json.append("  \"parameters\": [").append(parametersJson).append("],\n");
        json.append("  \"locals\": [").append(localsJson).append("],\n");
        json.append("  \"decompiler\": {");
        json.append("\"completed\": ").append(decompiled).append(", ");
        json.append("\"error\": ").append(q(decompileError)).append(", ");
        json.append("\"c\": ").append(q(decompiledC));
        json.append("},\n");
        json.append("  \"normalized_ir\": {");
        json.append("\"completed\": ").append(normalizedIr.completed).append(", ");
        json.append("\"error\": ").append(q(normalizedIr.error)).append(", ");
        json.append("\"operation_count\": ").append(normalizedIr.operationCount).append(", ");
        json.append("\"pcode_truncated\": ").append(normalizedIr.pcodeTruncated).append(", ");
        json.append("\"operations\": [").append(normalizedIr.operations).append("], ");
        json.append("\"basic_blocks\": [").append(normalizedIr.blocks).append("]");
        json.append("},\n");
        json.append("  \"bsim_signature\": {");
        json.append("\"completed\": ").append(bsimSignature.completed).append(", ");
        json.append("\"error\": ").append(q(bsimSignature.error)).append(", ");
        json.append("\"settings\": ").append(BSIM_SIGNATURE_SETTINGS).append(", ");
        json.append("\"feature_count\": ").append(bsimSignature.featureCount).append(", ");
        json.append("\"features\": [").append(bsimSignature.features).append("]");
        json.append("},\n");
        json.append("  \"basic_blocks\": [").append(blocksJson).append("],\n");
        json.append("  \"instructions\": [").append(instructionsJson).append("],\n");
        json.append("  \"calls\": [").append(String.join(",", calls)).append("],\n");
        json.append("  \"callers\": [").append(String.join(",", callers)).append("],\n");
        json.append("  \"data_refs\": [").append(String.join(",", dataRefs)).append("]\n");
        json.append("}\n");

        Path parent = outputPath.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        try (BufferedWriter writer = Files.newBufferedWriter(
                outputPath, StandardCharsets.UTF_8)) {
            writer.write(json.toString());
        }
        println("AUTODECOMP_GHIDRA_EVIDENCE=" + outputPath);
        println("AUTODECOMP_GHIDRA_FUNCTION=" + function.getEntryPoint());
        println("AUTODECOMP_GHIDRA_BLOCKS=" + blockCount);
        println("AUTODECOMP_GHIDRA_INSTRUCTIONS=" + instructionCount);
        println("AUTODECOMP_GHIDRA_HIGH_PCODE=" + normalizedIr.operationCount);
        println("AUTODECOMP_GHIDRA_BSIM_FEATURES=" + bsimSignature.featureCount);
    }

    private BsimSignature generateBsimSignature(Function function, int timeout) {
        BsimSignature result = new BsimSignature();
        DecompInterface decompiler = new DecompInterface();
        try {
            decompiler.setOptions(new DecompileOptions());
            decompiler.toggleSyntaxTree(false);
            decompiler.setSignatureSettings(BSIM_SIGNATURE_SETTINGS);
            if (!decompiler.openProgram(currentProgram)) {
                result.error = "failed to open program in BSim signature generator";
                return result;
            }
            SignatureResult signature =
                decompiler.generateSignatures(function, false, timeout, null);
            if (signature == null || signature.features == null) {
                result.error = "BSim signature generator returned no features";
                return result;
            }
            result.featureCount = signature.features.length;
            for (int i = 0; i < signature.features.length; i++) {
                if (i != 0) {
                    result.features.append(',');
                }
                result.features.append(q(String.format("%08x", signature.features[i])));
            }
            result.completed = true;
            return result;
        }
        catch (Exception error) {
            result.error = error.toString();
            return result;
        }
        finally {
            decompiler.dispose();
        }
    }

    private NormalizedIr decompileNormalized(Function function, int timeout) throws Exception {
        NormalizedIr result = new NormalizedIr();
        DecompInterface decompiler = new DecompInterface();
        try {
            decompiler.toggleCCode(false);
            decompiler.toggleSyntaxTree(true);
            decompiler.setSimplificationStyle("normalize");
            if (!decompiler.openProgram(currentProgram)) {
                result.error = "failed to open program in normalized decompiler";
                return result;
            }
            DecompileResults decompileResults =
                decompiler.decompileFunction(function, timeout, monitor);
            result.completed = decompileResults != null && decompileResults.decompileCompleted();
            result.error = decompileResults == null
                ? "normalized decompiler returned no result"
                : decompileResults.getErrorMessage();
            if (!result.completed) {
                return result;
            }
            HighFunction high = decompileResults.getHighFunction();
            if (high == null) {
                result.completed = false;
                result.error = "normalized decompiler returned no HighFunction";
                return result;
            }
            appendHighPcode(result, high);
            appendSemanticBlocks(result.blocks, high);
            return result;
        }
        finally {
            decompiler.dispose();
        }
    }

    private void appendHighPcode(NormalizedIr result, HighFunction high) throws Exception {
        Iterator<PcodeOpAST> operations = high.getPcodeOps();
        boolean first = true;
        while (operations.hasNext()) {
            monitor.checkCancelled();
            PcodeOpAST operation = operations.next();
            result.operationCount++;
            if (result.operationCount > MAX_HIGH_PCODE_OPS) {
                result.pcodeTruncated = true;
                continue;
            }
            if (!first) {
                result.operations.append(',');
            }
            first = false;
            result.operations.append("{\"seq\":").append(q(operation.getSeqnum().toString()));
            result.operations.append(",\"address\":")
                .append(q(operation.getSeqnum().getTarget().toString()));
            result.operations.append(",\"opcode\":").append(q(operation.getMnemonic()));
            result.operations.append(",\"output\":")
                .append(operation.getOutput() == null ? "null" : q(operation.getOutput().toString()));
            result.operations.append(",\"inputs\":[");
            for (int i = 0; i < operation.getNumInputs(); i++) {
                if (i != 0) {
                    result.operations.append(',');
                }
                result.operations.append(q(operation.getInput(i).toString()));
            }
            result.operations.append("]}");
        }
    }

    private void appendSemanticBlocks(StringBuilder out, HighFunction high) {
        boolean first = true;
        for (PcodeBlockBasic block : high.getBasicBlocks()) {
            if (!first) {
                out.append(',');
            }
            first = false;
            out.append("{\"index\":").append(block.getIndex());
            out.append(",\"start\":").append(q(block.getStart().toString()));
            out.append(",\"stop\":").append(q(block.getStop().toString()));
            out.append(",\"out\":[");
            for (int i = 0; i < block.getOutSize(); i++) {
                if (i != 0) {
                    out.append(',');
                }
                out.append(block.getOut(i).getIndex());
            }
            out.append("],\"in\":[");
            for (int i = 0; i < block.getInSize(); i++) {
                if (i != 0) {
                    out.append(',');
                }
                out.append(block.getIn(i).getIndex());
            }
            out.append("]}");
        }
    }

    private void appendParameters(StringBuilder out, Parameter[] parameters) {
        for (int i = 0; i < parameters.length; i++) {
            if (i != 0) {
                out.append(',');
            }
            Parameter parameter = parameters[i];
            out.append("{\"name\":").append(q(parameter.getName()));
            out.append(",\"ordinal\":").append(parameter.getOrdinal());
            appendVariableDetails(out, parameter);
            out.append('}');
        }
    }

    private void appendLocals(StringBuilder out, Variable[] locals) {
        for (int i = 0; i < locals.length; i++) {
            if (i != 0) {
                out.append(',');
            }
            Variable local = locals[i];
            out.append("{\"name\":").append(q(local.getName()));
            appendVariableDetails(out, local);
            out.append('}');
        }
    }

    private void appendVariableDetails(StringBuilder out, Variable variable) {
        out.append(",\"type\":").append(q(variable.getDataType().getDisplayName()));
        out.append(",\"length\":").append(variable.getLength());
        out.append(",\"storage\":").append(q(variable.getVariableStorage().toString()));
    }

    private void appendCallers(
            List<String> callers, FunctionManager functionManager, Function function) {
        Set<String> seen = new HashSet<>();
        ReferenceIterator references =
            currentProgram.getReferenceManager().getReferencesTo(function.getEntryPoint());
        while (references.hasNext()) {
            Reference reference = references.next();
            if (!reference.getReferenceType().isCall()) {
                continue;
            }
            Address site = reference.getFromAddress();
            Function caller = functionManager.getFunctionContaining(site);
            String entry = caller == null ? "" : caller.getEntryPoint().toString();
            String key = site + "|" + entry;
            if (!seen.add(key)) {
                continue;
            }
            callers.add("{\"site\":" + q(site.toString()) +
                ",\"entry\":" + q(entry) +
                ",\"ghidra_name\":" + q(caller == null ? "" : caller.getName()) + "}");
        }
    }

    private int appendBlocks(StringBuilder out, Function function) throws Exception {
        BasicBlockModel model = new BasicBlockModel(currentProgram);
        CodeBlockIterator blocks = model.getCodeBlocksContaining(function.getBody(), monitor);
        boolean firstBlock = true;
        int count = 0;
        while (blocks.hasNext()) {
            monitor.checkCancelled();
            CodeBlock block = blocks.next();
            if (!firstBlock) {
                out.append(',');
            }
            firstBlock = false;
            count++;
            out.append("{\"start\":").append(q(block.getFirstStartAddress().toString()));
            out.append(",\"end\":").append(q(block.getMaxAddress().toString()));
            out.append(",\"destinations\":[");
            CodeBlockReferenceIterator destinations = block.getDestinations(monitor);
            boolean firstDestination = true;
            while (destinations.hasNext()) {
                CodeBlockReference destination = destinations.next();
                if (!firstDestination) {
                    out.append(',');
                }
                firstDestination = false;
                out.append("{\"address\":")
                    .append(q(destination.getDestinationAddress().toString()));
                out.append(",\"flow\":")
                    .append(q(destination.getFlowType().toString())).append('}');
            }
            out.append("]}");
        }
        return count;
    }

    private int appendInstructions(
            StringBuilder out,
            Listing listing,
            FunctionManager functionManager,
            Function function,
            List<String> calls,
            List<String> dataRefs) throws Exception {
        InstructionIterator instructions = listing.getInstructions(function.getBody(), true);
        boolean firstInstruction = true;
        int count = 0;
        while (instructions.hasNext()) {
            monitor.checkCancelled();
            Instruction instruction = instructions.next();
            if (!firstInstruction) {
                out.append(',');
            }
            firstInstruction = false;
            count++;
            out.append("{\"address\":").append(q(instruction.getAddress().toString()));
            out.append(",\"mnemonic\":").append(q(instruction.getMnemonicString()));
            out.append(",\"text\":").append(q(instruction.toString()));
            out.append(",\"flow\":").append(q(instruction.getFlowType().toString()));
            out.append(",\"pcode\":[");
            PcodeOp[] pcode = instruction.getPcode();
            for (int i = 0; i < pcode.length; i++) {
                if (i != 0) {
                    out.append(',');
                }
                out.append(q(pcode[i].getMnemonic()));
            }
            out.append("],\"refs\":[");
            Reference[] references = instruction.getReferencesFrom();
            for (int i = 0; i < references.length; i++) {
                Reference reference = references[i];
                if (i != 0) {
                    out.append(',');
                }
                Address target = reference.getToAddress();
                out.append("{\"to\":").append(q(target.toString()));
                out.append(",\"type\":").append(q(reference.getReferenceType().toString()));
                out.append(",\"primary\":").append(reference.isPrimary()).append('}');

                if (reference.getReferenceType().isCall()) {
                    Function called = functionManager.getFunctionAt(target);
                    calls.add("{\"site\":" + q(instruction.getAddress().toString()) +
                        ",\"target\":" + q(target.toString()) +
                        ",\"ghidra_name\":" + q(called == null ? "" : called.getName()) + "}");
                }
                else if (reference.getReferenceType().isData()) {
                    Data data = listing.getDefinedDataAt(target);
                    String representation = data == null ? "" : data.getDefaultValueRepresentation();
                    dataRefs.add("{\"site\":" + q(instruction.getAddress().toString()) +
                        ",\"target\":" + q(target.toString()) +
                        ",\"representation\":" + q(representation) + "}");
                }
            }
            out.append("]}");
        }
        return count;
    }

    private static String q(String value) {
        if (value == null) {
            return "null";
        }
        StringBuilder out = new StringBuilder(value.length() + 2);
        out.append('"');
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            switch (ch) {
                case '\\': out.append("\\\\"); break;
                case '"': out.append("\\\""); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (ch < 0x20) {
                        out.append(String.format("\\u%04x", (int) ch));
                    }
                    else {
                        out.append(ch);
                    }
            }
        }
        return out.append('"').toString();
    }
}
