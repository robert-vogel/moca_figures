"""Utilities for figure generation.

By: Robert Vogel

Here I include wrappers around scikit-learn Gaussian Mixture
Model and Linear Discriminant Analysis so that they follow
the MOCA interface
"""
import re
import numpy as np
import os


from moca import classifiers as cls
from moca import cross_validate as cv
from moca import stats

from sklearn.mixture import GaussianMixture
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis


UMOCA_MAX_ITER=20000
UMOCA_TOL=1e-4


class Lda(cls.MocaABC):

    is_supervised = True

    def __init__(self):
        super().__init__()

        self._lda = LinearDiscriminantAnalysis(solver='lsqr',
                                               shrinkage='auto')

    def fit(self, data, labels):
        self.M = data.shape[0]
        self.prevalence = np.mean(labels)
        self._lda.fit(data.T, labels)

    def get_scores(self, data):
        if not stats.is_rank(data):
            raise ValueError

        return self._lda.decision_function(data.T)
        


class Gmm(cls.MocaABC):

    is_supervised = False

    def __init__(self):
        super().__init__()

        self._gmm = GaussianMixture(n_components=2,
                                    covariance_type="diag")

    def fit(self, data):
        self.M = data.shape[0]

        self._gmm.fit(data.T)

        # means_[components, features]
        # assume that component 1 is the positive class, positive class
        # samples have higher rank
        self.prevalence = None


        self._pos_idx = 1
        self._negative_idx = 0

        self.prevalence = self._gmm.weights_[self._pos_idx]

        delta = (self._gmm.means_[self._negative_idx, :]
                 - self._gmm.means_[self._pos_idx, :])

        delta = np.where(delta > 0, 1, 0)

        # if more than half the number of classifiers
        # have a positive delta, then switch the indexes
        if self.M/2 < np.sum(delta):
            self._pos_idx = 0
            self._negative_idx = 1
            self.prevalence = self._gmm.weights_[self._pos_idx]

    def get_scores(self, data):
        if not stats.is_rank(data):
            raise ValueError

        s = np.zeros(data.shape[1])
        # loop over samples
        for i in range(data.shape[1]):

            # loop over features, o.k., because covariance
            # is diagonal

            for j in range(data.shape[0]):

                # quadratic term
                s[i] += ((1/self._gmm.covariances_[self._pos_idx, j] 
                       - 1/self._gmm.covariances_[self._negative_idx, j]) 
                      * data[j,i]**2)

                # linear term
                s[i] += (2*(self._gmm.means_[self._negative_idx, j]
                            / self._gmm.covariances_[self._negative_idx,j]
                            - self._gmm.means_[self._pos_idx, j]
                            / self._gmm.covariances_[self._pos_idx, j])
                         * data[j, i])

        return s


def read_data(fname, method_regex,
              datum_regex="^[+-]?[0-9]*\.?[0-9]*E?e?[+-]?[0-9]*$"):
    """Load data under the assumed specification.

    Data specification:

        comma delimited text file as follows

        Line 1: header labels:
            * sample_ids
                may be multiple columns and have a variety
                of sample id names
            * method identifier
                the sample predictions of method in format of
                method_regex.
            * class
                sample class labels
        Line 2-end
            * arbirary sample names,
            * sample scores per team, or empty -> np.nan
            * sample NaN -> np.nan
            * sample class labels, 0 or 1

    Args:
        fname: (str)
            name of file, including path, to be read
        method_regex: (str)
            regular expression for identifying methods

    Return:
        X: ((n samples, m classifiers) np.ndarray)
            Sample scores by each base classifier
        y: ((n samples,) np.ndarray)
            sample class labels, 0 and 1, representing the
            negative and positive class labels, respectively. 
        cls_names: (list)
            classifier team names in order of data matrix
    """
    delim = ","


    with open(fname, "r") as fid:

        # Decompose header and get the number of samples
        cls_names = []
        score_col_idx = []
        label_col_idx = None

        for i, tline in enumerate(fid):

            if i == 0:

                tline = tline.strip().split(delim)

                for j, field in enumerate(tline):

                    if re.match("^class$", field):
                        label_col_idx = j
                        continue

                    if field in cls_names:
                        raise ValueError("Duplicate team names")

                    if (re.match(method_regex, field)
                        is not None):
                        cls_names.append(field)
                        score_col_idx.append(j)

                if ((m_cls := len(score_col_idx)) == 0
                        or label_col_idx is None):
                    raise ValueError("Could not decompose header")

        n_samples = i

        X = np.zeros(shape=(m_cls, n_samples))
        y = np.zeros(n_samples)

        # return file object to the beginning of file
        fid.seek(0, os.SEEK_SET)


        sample_idx = 0
        for i, tline in enumerate(fid):
            
            if i == 0:
                continue

            tline = tline.strip().split(delim)

            if (re.match(datum_regex, tline[label_col_idx])
                is None):
                raise ValueError(("Incorrect sample label"
                                 f" {tline[label_col_idx]}"))

            y[sample_idx] = np.float16(tline[label_col_idx])


            for j, cls_idx in enumerate(score_col_idx):
                val = tline[cls_idx]

                if val == "":
                    X[j, sample_idx] = np.nan
                elif re.match(datum_regex, val) is not None:
                    X[j, sample_idx] = np.float64(val)
                elif (re.match("^NaN$", val) is not None
                      or val == ""):
                    X[j, sample_idx] = np.nan
                else:
                    raise ValueError(("Error while parsing"
                                      f" file at line {i}"
                                      f"\r {','.join(tline)}"))

            sample_idx += 1

    return X, y, cls_names


def auc_by_stratified_cv(data, labels, moca_cls, kfolds=5, seed=None):
    """Compute AUC by stratified cross-validation
    
    Args:
        data : ((m classifiers, n samples) np.ndarray)
            sample rank predictions per base classifier
        labels : ((n samples,) np.ndarray)
            sample class labels, 0 and 1, representing the
            negative and positive class labels, respectively. 
        moca_cls: (list)
            list of moca compatible classifier instances
        kfolds: (int)
            number of folds for cross validations, > 0,
            default=5
        stratified_cv: (bool)
            if true, perform cross-validation such that the
            sample class prevalence is, approximately, preserved.
        seed: (np.random.default_rng compatible seed)
            default None

    Returns:
        auc:((k folds, n classifiers) np.ndarray)
        cl_labels: (list)
            classifier labels for plotting.  In order of 
            columns in auc np.ndarray
    """
    rng = np.random.default_rng(seed=seed)

    cl_labels = []

    auc = np.zeros(shape=(kfolds, len(moca_cls)))

    for i, cl in enumerate(moca_cls):

        cl_labels.append(cl.name)

        cv_generator = cv.stratified_kfold(data, labels, kfolds, seed=rng)

        k = 0
        for train, test in cv_generator:

            if cl.is_supervised:
                cl.fit(train["data"], train["labels"])
            else:
                cl.fit(train["data"])

            _, _, auc[k, i] = stats.roc(cl.get_scores(test["data"]),
                                        test["labels"])
            k += 1

    cv_generator = cv.stratified_kfold(data, labels, kfolds, seed=rng)

    return auc, cl_labels
    

def cov2corr(c):
    corr= np.zeros(shape=c.shape)
    for i in range(corr.shape[0]):
        for j in range(1, corr.shape[0]):
            corr[i, j] = c[i,j] / np.sqrt(c[i, i] * c[j, j])
    return corr + corr.T + np.eye(c.shape[0])