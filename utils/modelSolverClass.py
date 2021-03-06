# ====================================================================================
# Class to simulate spheroid growth using one of 6 ODE models
# ====================================================================================
import numpy as np
import scipy.integrate
import pandas as pd
import math
import os
import sys
if 'matplotlib' not in sys.modules:
    import matplotlib as mpl
    mpl.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
sns.set(style="white")
import contextlib
import sys
import myUtils as utils
# ====================================================================================
class TumourModelClass():
    def __init__(self, growthFunIdx=2, phenotypicSwitchingB=True, areaModel='linear', **kwargs):
        # Initialise parameters
        self.nParams = 5 # Number of parameters in the model. Have at least 1 for the drug kill
        self.paramDic = {"r1": 1., "r2": 1., "d": 0.5, "S0": 5000, "R0": 100, "DMax":100}
        self.resultsDf = None

        # Assign the growth function. The options are: [1=Exponential Growth, 2=Logistic Growth, 3=Surface Growth]
        # If none is given, it will default to logistic growth.
        self.growthFunIdx = growthFunIdx
        if self.growthFunIdx == 1:
            self.GrowthFun_S = lambda N1, N2, D: self.paramDic['r1'] * N1
            self.GrowthFun_R = lambda N1, N2, D: self.paramDic['r2'] * N2
            self.nParams += 2
        elif self.growthFunIdx == 2:
            self.paramDic = {**self.paramDic,"K": 1e7}
            self.GrowthFun_S = lambda N1, N2, D: self.paramDic['r1'] * (1 - (N1 + N2) / self.paramDic['K']) * N1
            self.GrowthFun_R = lambda N1, N2, D: self.paramDic['r2'] * (1 - (N1 + N2) / self.paramDic['K']) * N2
            self.nParams += 3
        elif self.growthFunIdx == 3:
            self.GrowthFun_S = lambda N1, N2, D: self.paramDic['r1'] * N1 / ((N1 + N2) ** (1 / 3))
            self.GrowthFun_R = lambda N1, N2, D: self.paramDic['r2'] * N2 / ((N1 + N2) ** (1 / 3))
            self.nParams += 2
        elif self.growthFunIdx == 4:
            self.paramDic = {**self.paramDic,"K": 1e7}
            self.GrowthFun_S = lambda N1, N2, D: self.paramDic['r1'] * (1 - (N1 + N2) / self.paramDic['K']) * N1 * (1 - self.paramDic['d']*D/self.paramDic['DMax'])
            self.GrowthFun_R = lambda N1, N2, D: self.paramDic['r2'] * (1 - (N1 + N2) / self.paramDic['K']) * N2
            self.nParams += 3
        elif self.growthFunIdx == 5:
            self.paramDic = {**self.paramDic,"K": 1e7,"r1_hd":self.paramDic['r1']}
            self.GrowthFun_S = lambda N1, N2, D: (self.paramDic['r1']*(N1<50)+self.paramDic['r1_hd']*(N1>=50)) * (1 - (N1 + N2) / self.paramDic['K']) * N1
            self.GrowthFun_R = lambda N1, N2, D: 0
            self.nParams += 2
        elif self.growthFunIdx == 6:
            self.paramDic = {**self.paramDic,"K": 1e7,"gamma":0}
            self.GrowthFun_S = lambda N1, N2, D: self.paramDic['r1'] * (1 - N1 / self.paramDic['K']) * N1 * (1 - self.paramDic['d']*D/self.paramDic['DMax']) - self.paramDic['gamma'] * N1
            self.GrowthFun_R = lambda N1, N2, D: 0
            self.nParams += 2

        # Assign the function for phenotypic switching [0=No Switching, 1=Switching (Default)]
        self.phenotypicSwitchingB = phenotypicSwitchingB
        if self.phenotypicSwitchingB:
            self.paramDic = {**self.paramDic,"reSensitRate": 0.1, "resAcquisRate": 0.1}
            self.PhenotypicSwitchingFun = lambda S, R, D: self.paramDic['reSensitRate'] * (1 - D/self.paramDic['DMax']) * R - self.paramDic[
                'resAcquisRate'] * S * D/self.paramDic['DMax']
            self.nParams += 2
        else:
            self.PhenotypicSwitchingFun = lambda S, R, D: 0

        # Choose the area model used to convert cell counts to the observed fluorescent area
        self.areaModel = areaModel
        if self.areaModel=='linear': self.paramDic = {**self.paramDic, "scaleFactor":1.}

        # Set the parameters
        self.SetParams(**kwargs)

        # Configure the solver
        self.dt = kwargs.get('dt', 1e-3)  # Time resolution to return the model prediction on
        self.absErr = kwargs.get('absErr', 1.0e-8)  # Absolute error allowed for ODE solver
        self.relErr = kwargs.get('relErr', 1.0e-6)  # Relative error allowed for ODE solver
        self.solverMethod = kwargs.get('method', 'RK45')  # ODE solver used
        self.suppressOutputB = kwargs.get('suppressOutputB',
                                          False)  # If true, suppress output of ODE solver (including warning messages)
        self.successB = False  # Indicate successful solution of the ODE system

    # =========================================================================================
    # Function to set the parameters
    def SetParams(self, **kwargs):
        for key in self.paramDic.keys():
            self.paramDic[key] = float(kwargs.get(key, self.paramDic[key]))
        self.initialStateList = [self.paramDic['S0'], self.paramDic['R0']]

    # =========================================================================================
    # The governing equations
    def ModelEqns(self, t, uVec):
        S, R, D = uVec
        dudtVec = np.zeros_like(uVec)
        dudtVec[0] = self.GrowthFun_S(S, R, D) + self.PhenotypicSwitchingFun(S, R, D) - self.paramDic['d'] * D/self.paramDic['DMax'] * S * (self.growthFunIdx!=4) * (self.growthFunIdx!=6)
        dudtVec[1] = self.GrowthFun_R(S, R, D) - self.PhenotypicSwitchingFun(S, R, D)
        dudtVec[2] = 0
        return (dudtVec)

    # =========================================================================================
    # Function to simulate the model
    def Simulate(self, treatmentScheduleList, **kwargs):
        # Allow configuring the solver at this point as well
        self.dt = float(kwargs.get('dt', self.dt))  # Time resolution to return the model prediction on
        self.absErr = kwargs.get('absErr', self.absErr)  # Absolute error allowed for ODE solver
        self.relErr = kwargs.get('relErr', self.relErr)  # Relative error allowed for ODE solver
        self.solverMethod = kwargs.get('method', self.solverMethod) # ODE solver used
        self.successB = False  # Indicate successful solution of the ODE system
        self.suppressOutputB = kwargs.get('suppressOutputB',
                                          self.suppressOutputB)  # If true, suppress output of ODE solver (including warning messages)

        # Solve
        self.treatmentScheduleList = treatmentScheduleList
        if self.resultsDf is None or treatmentScheduleList[0][0]==0:
            currStateVec = self.initialStateList + [0]
            self.resultsDf = None
        else:
            currStateVec = [self.resultsDf['S'].iloc[-1], self.resultsDf['R'].iloc[-1], self.resultsDf['DrugConcentration'].iloc[-1]]
        resultsDFList = []
        encounteredProblemB = False
        for intervalId, interval in enumerate(treatmentScheduleList):
            tVec = np.arange(interval[0], interval[1], self.dt)
            if intervalId == (len(treatmentScheduleList) - 1):
                tVec = np.arange(interval[0], interval[1] + self.dt, self.dt)
            currStateVec[2] = interval[2]
            if self.suppressOutputB:
                with stdout_redirected():
                    solObj = scipy.integrate.solve_ivp(self.ModelEqns, y0=currStateVec,
                                                       t_span=(tVec[0], tVec[-1] + self.dt), t_eval=tVec,
                                                       method=self.solverMethod,
                                                       atol=self.absErr, rtol=self.relErr,
                                                       max_step=kwargs.get('max_step', np.inf))
            else:
                solObj = scipy.integrate.solve_ivp(self.ModelEqns, y0=currStateVec,
                                                   t_span=(tVec[0], tVec[-1] + self.dt), t_eval=tVec,
                                                   method=self.solverMethod,
                                                   atol=self.absErr, rtol=self.relErr,
                                                   max_step=kwargs.get('max_step', np.inf))
            # Check that the solver converged
            if not solObj.success or np.any(solObj.y<0):
                self.errMessage = solObj.message
                encounteredProblemB = True
                print("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
                if not solObj.success:
                    print(self.errMessage)
                else:
                    print("Negative values encountered in the solution. Make the time step smaller or consider using a stiff solver.")
                print("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
                self.solObj = solObj
                break
            # Save results
            resultsDFList.append(
                pd.DataFrame({"Time": tVec, "S": solObj.y[0, :], "R": solObj.y[1, :], "DrugConcentration": solObj.y[2, :]}))
            currStateVec = solObj.y[:, -1]
        # If the solver diverges in the first interval, it can't return any solution. Catch this here, and in this case
        # replace the solution with all zeros.
        if len(resultsDFList)>0:
            resultsDf = pd.concat(resultsDFList)
        else:
            resultsDf = pd.DataFrame({"Time": tVec, "S": np.zeros_like(tVec),
                                        "R": np.zeros_like(tVec), "DrugConcentration": np.zeros_like(tVec)})
        # Compute the fluorescent area that we'll see
        resultsDf['TumourSize'] = pd.Series(self.RunCellCountToFluorescentAreaModel(resultsDf),
                                            index=resultsDf.index)
        if self.resultsDf is not None:
            resultsDf = pd.concat([self.resultsDf,resultsDf])
        self.resultsDf = resultsDf
        self.successB = True if not encounteredProblemB else False

    # =========================================================================================
    # Define the model mapping cell counts to observed fluorescent area
    def RunCellCountToFluorescentAreaModel(self, popModelSolDf):
        if self.areaModel=='linear': # Assume a linear relationship between cell count and area
            return self.paramDic['scaleFactor']*(popModelSolDf.R.values + popModelSolDf.S.values)
        elif self.areaModel=='sphere': # Map cell count into a sphere
            volPerCell = 4. / 3 * math.pi * math.pow(10,
                                                     3)  # Volume per cell in the spheroid. Assume a sphere with diameter 20um (https://www.nexcelom.com/training-and-support/white-papers/accurately-measure-cell-size-of-nci-60-cancer-cell-lines/)
            # Obtain total cell count
            totalCellsVec = popModelSolDf.R.values + popModelSolDf.S.values
            # Convert to area
            return math.pow(3. / 4 * math.sqrt(math.pi) * volPerCell, 2. / 3) * np.power(totalCellsVec, 2. / 3)

    # =========================================================================================
    # Function to plot the model predictions
    def Plot(self, decoratey2=True, ax=None, **kwargs):
        if ax is None: fig, ax = plt.subplots(1,1)
        lnslist = []
        # Plot the area the we will see on the images
        if kwargs.get('plotAreaB', True):
            lnslist += ax.plot(self.resultsDf['Time'],
                                self.resultsDf['TumourSize'],
                                lw=kwargs.get('linewidthA', 4), color=kwargs.get('colorA', 'b'),
                                linestyle=kwargs.get('linestyleA', '-'), marker=kwargs.get('markerA', None),
                                label=kwargs.get('labelA', 'Model Prediction'))

        # Plot the individual populations
        if kwargs.get('plotPops', False):
            propS = self.resultsDf['S'].values / (self.resultsDf['S'].values + self.resultsDf['R'].values)
            lnslist += ax.plot(self.resultsDf['Time'],
                                propS * self.resultsDf['TumourSize'],
                                lw=kwargs.get('linewidth', 4), linestyle=kwargs.get('linestyleS', '--'),
                                color=kwargs.get('colorS', 'g'),
                                label='S')
            lnslist += ax.plot(self.resultsDf['Time'],
                                (1 - propS) * self.resultsDf['TumourSize'],
                                lw=kwargs.get('linewidth', 4), linestyle=kwargs.get('linestyleR', '--'),
                                color=kwargs.get('colorR', 'r'),
                                label='R')

            # Plot the drug concentration
        ax2 = ax.twinx()  # instantiate a second axes that shares the same x-axis
        drugConcentrationVec = utils.TreatmentListToTS(treatmentList=utils.ExtractTreatmentFromDf(self.resultsDf),
                                                 tVec=self.resultsDf['Time'])
        ax2.fill_between(self.resultsDf['Time'],
                         0, drugConcentrationVec, color="#8f59e0", alpha=0.2, label="Drug Concentration")
        # Format the plot
        ax.set_xlim([0, kwargs.get('xlim', 1.1*self.resultsDf['Time'].max())])
        ax.set_ylim([kwargs.get('yMin',-1.1*np.abs(self.resultsDf['TumourSize'].min())), kwargs.get('ylim', 1.1*self.resultsDf['TumourSize'].max())])
        ax2.set_ylim([0, kwargs.get('y2lim', self.resultsDf['DrugConcentration'].max()+.1)])
        ax.set_xlabel("Time")
        ax.set_ylabel("Tumour Size")
        ax2.set_ylabel(r"Drug Concentration in $\mu M$" if decoratey2 else "")
        ax.set_title(kwargs.get('title', ''))
        if kwargs.get('plotLegendB', True):
            labsList = [l.get_label() for l in lnslist]
            plt.legend(lnslist, labsList, loc=kwargs.get('legendLoc', "upper right"))
        plt.tight_layout()
        if kwargs.get('saveFigB', False):
            plt.savefig(kwargs.get('outName', 'modelPrediction.png'), orientation='portrait', format='png')
            plt.close()
        if kwargs.get('returnAx', False): return ax


# ====================================================================================
# Functions used to suppress output from odeint
# Taken from: https://stackoverflow.com/questions/31681946/disable-warnings-originating-from-scipy
def fileno(file_or_fd):
    fd = getattr(file_or_fd, 'fileno', lambda: file_or_fd)()
    if not isinstance(fd, int):
        raise ValueError("Expected a file (`.fileno()`) or a file descriptor")
    return fd


@contextlib.contextmanager
def stdout_redirected(to=os.devnull, stdout=None):
    """
    https://stackoverflow.com/a/22434262/190597 (J.F. Sebastian)
    """
    if stdout is None:
        stdout = sys.stdout

    stdout_fd = fileno(stdout)
    # copy stdout_fd before it is overwritten
    # NOTE: `copied` is inheritable on Windows when duplicating a standard stream
    with os.fdopen(os.dup(stdout_fd), 'wb') as copied:
        stdout.flush()  # flush library buffers that dup2 knows nothing about
        try:
            os.dup2(fileno(to), stdout_fd)  # $ exec >&to
        except ValueError:  # filename
            with open(to, 'wb') as to_file:
                os.dup2(to_file.fileno(), stdout_fd)  # $ exec > to
        try:
            yield stdout  # allow code to be run with the redirected stdout
        finally:
            # restore stdout to its previous value
            # NOTE: dup2 makes stdout_fd inheritable unconditionally
            stdout.flush()
            os.dup2(copied.fileno(), stdout_fd)  # $ exec >&copied
