package Framework.Tools.MultiWellExperiment;

@FunctionalInterface
public interface StepWell<T> {
    void Step(T model,int iWell);
}
