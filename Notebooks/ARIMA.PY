#ECONOMETRIA FINANCIERA
# MODELOS ARIMA Y MODELO EWMA

install.packages("forecast")
install.packages("data.table")
install.packages("TTR")
install.packages("Metrics")
install.packages("fBasics")
install.packages("urca")
install.packages("dynlm")
install.packages("mFilter")
install.packages("FinTS")
install.packages("MTS")
install.packages("rugarch")
install.packages("ggrepel")
install.packages("moments")
install.packages("MASS")

library(mFilter)
library(forecast)
library(lmtest)
library(ggplot2)
library(tseries)
library(stats)
library(fBasics)
library(grid)
library(openxlsx)
library(dynlm)
library(urca)
library(FinTS)
library(MTS)
library(fGarch)
library(rugarch)
library(ggrepel)

#---------------------------------------------------------------------------------------------------------------

library(readxl)
Data_Econometria_Financiera_2026_1 <- read_excel("~/DOCUMENTOS PORTATIL FREDY/ECONOMETRÍA FINANCIERA/CLASE EF 2026-1/Data Econometria Financiera 2026-1.xlsx", 
                                                 sheet = "REG ORO")
View(Data_Econometria_Financiera_2026_1)

#----------------------------------------------------------------------------------------------------------------------------------

library(readxl)
Data_Econometria_Financiera_2026_1 <- read_excel("Data Econometria Financiera 2026-1.xlsx", 
                                                 sheet = "REG ORO")
View(Data_Econometria_Financiera_2026_1)


#---------------------------------------------------------------------------------------------------------------------------


#---------------------------#
# Metodologia Box-Jenkins 
# Modelo ARIMA - 
#---------------------------#
#----------------------------------

###############
#PASO 1 -> Identificacion
###############


#Analisis lnAPPLE
Y=Data_Econometria_Financiera_2026_1$AAPL
Y<-ts(Y)
Y<-head(Y,1806)

autoplot(Y)

#----------------------------------------------------------------------------------------------------------

#Transformacion Box Cox (Estacionariedad del proceso en varianza)
lambda <- BoxCox.lambda(Y)
lambda

apple_trans<-BoxCox(Y,lambda)

#verificar la serie transformada
autoplot(apple_trans, main="serie de tiempo transformada")

#----------------------------------------------------------------------------------------------------------

#Transformacion Box Cox CON LAMBDA=0 (Estacionariedad del proceso en varianza)
#LOGARITMO NATURAL DE APPLE
lnY=log(Y)
autoplot(lnY)


# Diferenciacion lnapple (Estacionariedad en media)
rt_Y=diff(lnY,differences=1)
autoplot(rt_Y)

#--------------------------------------------------------------------------------------------------

# Veamos si los retornos son incorrelacionados
#ACF Y PACF DE RAPPLE PARA IDENTIFICAR EL ORDEN DEL MODELO AR Y DEL MODELO MA
acf_rt <- acf(rt_Y, col = "blue", type = "correlation", plot = T)
pacf_rt <- acf(rt_Y, col = "blue", type = "partial", plot = T)


#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_rt <- data.frame(lag = acf_rt$lag[-1],  # Excluir lag 0
                     acf = acf_rt$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_rt, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(rt_Y)), 1.96/sqrt(length(rt_Y))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de rt_Y (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#REALIZAR EL PACF 
pacf_rt <- data.frame(lag = pacf_rt$lag, 
                      pacf = pacf_rt$acf)  

# Crear el correlograma parcial con ggplot2
ggplot(pacf_rt, aes(x = lag, y = pacf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(rt_Y)), 1.96/sqrt(length(rt_Y))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma parcial de rt_Y",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelación parcial") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes



#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(rt_Y, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(rt_Y, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

#--------------------------------------------------------------------------------------------------------------------
###############
#PASO 2 -> Estimacion
###############

#----------------------Modelos AUTORREGRESIVOS---------------------------------------------

m1=arima(rt_Y, order = c(1,0,0),include.mean = TRUE,
         method = "CSS-ML",
         optim.method = "BFGS") # ("Nelder-Mead", "BFGS", "CG")
summary(m1)
coeftest(m1)

#Medidas de dependencia temporal-Autocorrelacion
residuals_m1<-residuals(m1)

checkresiduals(m1)

#ACF DE LOS RESIDUOS
acf_resid <- acf(residuals_m1, col = "blue", type = "correlation", plot = T)


#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m1)), 1.96/sqrt(length(residuals_m1))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m1 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#writeClipboard(as.character(residuals_m1))


#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m1, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m1, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados


# Calcular los valores de la ACF
resultado_acf_et_m1 <- acf(residuals_m1)

# Ver los valores de la ACF
resultado_acf_et_m1$acf


# Estimar el modelo AUTO ARIMA
modelo_arima <- auto.arima(rt_Y)

# Mostrar los resultados del modelo
summary(modelo_arima)

checkresiduals(modelo_arima)

ensayo=arima(rt_Y, order = c(8,0,0))
ensayo
coeftest(ensayo)

#------------------------------------------
#Modelo 2
#------------------------------------------
#Estimar el AR(1), AR(8)
m2 <- Arima(rt_Y, order=c(8,0,0), fixed=c(NA, rep(0,6),NA, NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m2)
coeftest(m2)

#Medidas de dependencia temporal-Autocorrelacion
residuals_m2<-residuals(m2)


acf_resid <- acf(residuals_m2, col = "blue", type = "correlation", plot = T)


#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m2)), 1.96/sqrt(length(residuals_m2))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m2 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m2, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m2, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

# Calcular la ACF
resultado_acf_et_m2 <- acf(residuals_m2)

# Ver los valores de la ACF
resultado_acf_et_m2$acf


#------------------------------------------
#Modelo 3
#------------------------------------------
#Estimar el ar(1,8,9)
m3 <- Arima(rt_Y, order=c(9,0,0), fixed=c(NA, rep(0,6),NA,NA, NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m3)
coeftest(m3)

#Medidas de dependencia temporal-Autocorrelacion
residuals_m3<-residuals(m3)
#residuals_m3

writeClipboard(as.character(residuals_m3))

checkresiduals(m3)

acf_resid <- acf(residuals_m3, col = "blue", type = "correlation", plot = T)


#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m3)), 1.96/sqrt(length(residuals_m3))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m3 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes



#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m3, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m3, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

# Calcular la ACF
resultado_acf_et_m3 <- acf(residuals_m3)

# Ver los valores de la ACF
resultado_acf_et_m3$acf



#------------------------------------------
#Modelo 4
#------------------------------------------
#Estimar el ar(1,8,9,22)
m4 <- Arima(rt_Y, order=c(22,0,0), fixed=c(NA, rep(0,6),NA,NA, rep(0,12),NA,NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m4)
coeftest(m4)

#Medidas de dependencia temporal-Autocorrelacion
residuals_m4<-residuals(m4)


writeClipboard(as.character(residuals_m4))

checkresiduals(m3)

acf_resid <- acf(residuals_m4, col = "blue", type = "correlation", plot = T)


#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m4)), 1.96/sqrt(length(residuals_m4))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m4 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes



#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m4, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m4, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

# Calcular la ACF
resultado_acf_et_m4 <- acf(residuals_m4)

# Ver los valores de la ACF
resultado_acf_et_m4$acf

#----------------------------------------------------------------------------------------------------------

# -----------------------------MODELOS DE MEDIAS MOVILES----------------------------------------------------------

#Modelo 5 
#------------------------------------------------------------------------------------------------------
#ESTIMAR UN MODELO MA(1)
m5=arima(rt_Y, order = c(0,0,1),include.mean = TRUE,
         method = "CSS-ML",
         optim.method = "BFGS")
summary(m5)
coeftest(m5)


#Medidas de dependencia temporal-Autocorrelacion
residuals_m5<-residuals(m5)


acf_resid <- acf(residuals_m4, col = "blue", type = "correlation", plot = T)

#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m5)), 1.96/sqrt(length(residuals_m5))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m5 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m5, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m5, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

# Calcular la ACF
resultado_acf_et_m5 <- acf(residuals_m5)

# Ver los valores de la ACF
resultado_acf_et_m5$acf

#--------------------------------------------------------------------------------------------------------------------------

#------------------------------------------
#Modelo 6
#------------------------------------------
#ESTIMAR UN MODELO MA(1) MA(8)

#MODELO MA(1,8)
m6 <- Arima(rt_Y, order=c(0,0,8), fixed=c(NA, rep(0,6),NA,NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m6)
coeftest(m6)


#Medidas de dependencia temporal-Autocorrelacion
residuals_m6<-residuals(m6)


acf_resid <- acf(residuals_m6, col = "blue", type = "correlation", plot = T)

#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m6)), 1.96/sqrt(length(residuals_m6))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m6 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes



#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m6, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m6, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

# Calcular la ACF
resultado_acf_et_m6 <- acf(residuals_m6)

# Ver los valores de la ACF
resultado_acf_et_m6$acf

#-------------------------------------------------------------------------------------------------------
#Modelo 7
#------------------------------------------
#ESTIMAR UN MODELO MA(1,8,9)
m7 <- Arima(rt_Y, order=c(0,0,9), fixed=c(NA, rep(0,6),NA,NA, NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m7)
coeftest(m7)


#Medidas de dependencia temporal-Autocorrelacion
residuals_m7<-residuals(m7)


acf_resid <- acf(residuals_m7, col = "blue", type = "correlation", plot = T)

#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m7)), 1.96/sqrt(length(residuals_m7))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m7 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m7, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m7, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

# Calcular la ACF
resultado_acf_et_m7 <- acf(residuals_m7)

# Ver los valores de la ACF
resultado_acf_et_m7$acf

#-------------------------------------------------------------------------------------------------------------

#-------------------------------------------------------------------------------------------------------
#Modelo 8
#------------------------------------------
#ESTIMAR UN MODELO MA(1,8,9,22)
m8 <- Arima(rt_Y, order=c(0,0,22), fixed=c( NA,rep(0,6),NA,NA,rep(0,12),NA, NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m8)
coeftest(m8)


#Medidas de dependencia temporal-Autocorrelacion
residuals_m8<-residuals(m8)


acf_resid <- acf(residuals_m8, col = "blue", type = "correlation", plot = T)

#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m8)), 1.96/sqrt(length(residuals_m8))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m8 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m8, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m8, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados

# Calcular la ACF
resultado_acf_et_m8 <- acf(residuals_m8)

# Ver los valores de la ACF
resultado_acf_et_m8$acf


#----------------------------------------------------------------------------------------------------------------------

# -----------------------------MODELOS ARMA----------------------------------------------------------

#ESTIMAR UN Modelo AR(1,8,9) MA(22)
#------------------------------------------
m9 <- Arima(rt_Y, order=c(9,0,22), fixed=c(NA, rep(0,6),NA,NA, c(rep(0,21),NA), NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m9)
coeftest(m9)


#Medidas de dependencia temporal-Autocorrelacion
residuals_m9<-residuals(m9)

acf_resid <- acf(residuals_m9, col = "blue", type = "correlation", plot = T)

#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m9)), 1.96/sqrt(length(residuals_m9))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m9 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m9, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m9, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados


# Calcular la ACF
resultado_acf_et_m9 <- acf(residuals_m9)

# Ver los valores de la ACF
resultado_acf_et_m9$acf

#----------------------------------------------------------------------------------------------

#ESTIMAR UN Modelo AR(1,8) MA(9,22)
#------------------------------------------
m10 <- Arima(rt_Y, order=c(8,0,22), fixed=c(NA,rep(0,6),NA,c(rep(0,8),NA,rep(0,12),NA), NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m10)
coeftest(m10)


#Medidas de dependencia temporal-Autocorrelacion
residuals_m10<-residuals(m10)

acf_resid <- acf(residuals_m10, col = "blue", type = "correlation", plot = T)

#REALIZAR EL ACF SIN INCLUIR EL REZAGO 0
acf_resid <- data.frame(lag = acf_resid$lag[-1],  # Excluir lag 0
                        acf = acf_resid$acf[-1])  # Excluir el valor de ACF en lag 0

# Crear el correlograma con ggplot2
ggplot(acf_resid, aes(x = lag, y = acf)) +
  geom_bar(stat = "identity", fill = "#69b3a2", color = "black", width = 0.1) +  # Barras del correlograma
  geom_hline(yintercept = c(-1.96/sqrt(length(residuals_m10)), 1.96/sqrt(length(residuals_m10))),  # Líneas de confianza
             linetype = "dashed", color = "red") +
  theme_minimal() +                           # Tema minimalista para el gráfico
  labs(title = "Correlograma de residuals_m10 (excluyendo Lag 0)",  # Título
       x = "Lag",                             # Etiqueta del eje x
       y = "Autocorrelacion") +               # Etiqueta del eje y
  theme(plot.title = element_text(hjust = 0.5),  # Centrar el título
        axis.text = element_text(size = 12),     # Tamaño de las etiquetas de los ejes
        axis.title = element_text(size = 14))    # Tamaño de los títulos de los ejes


#LJUNG-BOX MULTI-REZAGOS
# ===============================

rezagos <- 1:36

tabla_resultados <- data.frame(
  Rezago = rezagos,
  Estadistico = sapply(rezagos, function(l)
    Box.test(residuals_m10, lag = l, type = "Ljung-Box")$statistic),
  p_valor = sapply(rezagos, function(l)
    Box.test(residuals_m10, lag = l, type = "Ljung-Box")$p.value)
)

tabla_resultados


# Calcular la ACF
resultado_acf_et_m10 <- acf(residuals_m10)

# Ver los valores de la ACF
resultado_acf_et_m10$acf


############################################################################
#PASO 3 -> validacion, diagnostico
#------------------------------------------------------------------------------
#Mejor modelo basados en AIC
AIC(m4)
AIC(m8)
AIC(m9)
AIC(m10)
BIC(m4)
BIC(m8)
BIC(m9)
BIC(m10)

# Calcula el logaritmo de la verosimilitud
log_verosimilitud_m4 <- logLik(m4)

# Imprime el logaritmo de la verosimilitud
print(log_verosimilitud_m4)


# Calcula el logaritmo de la verosimilitud
log_verosimilitud_m8 <- logLik(m8)

# Imprime el logaritmo de la verosimilitud
print(log_verosimilitud_m8)


# Calcula el logaritmo de la verosimilitud
log_verosimilitud_m9 <- logLik(m9)

# Imprime el logaritmo de la verosimilitud
print(log_verosimilitud_m9)

# Calcula el logaritmo de la verosimilitud
log_verosimilitud_m10 <- logLik(m10)

# Imprime el logaritmo de la verosimilitud
print(log_verosimilitud_m10)

# Obtener el número de parámetros estimados (p + d + q + número de parámetros de la constante)
k <- length(coef(m4)) + 1  # Incluye el intercepto si está presente

# Obtener el tamaño de la muestra
n <- length(rt_Y)

# Calcular HQIC_m4
HQIC_m4 <- -2 * as.numeric(log_verosimilitud_m4) + 2 * k * log(log(n))
HQIC_m4

#Calcular HQIC_m8
HQIC_m8 <- -2 * as.numeric(log_verosimilitud_m8) + 2 * k * log(log(n))
HQIC_m8

#Calcular HQIC_m9
HQIC_m9 <- -2 * as.numeric(log_verosimilitud_m9) + 2 * k * log(log(n))
HQIC_m9

#Calcular HQIC_m10
HQIC_m10 <- -2 * as.numeric(log_verosimilitud_m10) + 2 * k * log(log(n))
HQIC_m10

# Calcular la suma de los cuadrados de los errores
sse_m4 <- sum(residuals_m4^2)

# Mostrar el resultado
print(sse_m4)

# Calcular la suma de los cuadrados de los errores
sse_m8 <- sum(residuals_m8^2)

# Mostrar el resultado
print(sse_m8)

# Calcular la suma de los cuadrados de los errores
sse_m9 <- sum(residuals_m9^2)

# Mostrar el resultado
print(sse_m9)

# Calcular la suma de los cuadrados de los errores
sse_m10 <- sum(residuals_m10^2)

# Mostrar el resultado
print(sse_m10)


#------------------------------------------------------------------------------------------------------------------------
#---------------------------------------------------------------------------------------------------------------------------------

#PASO 4 -> Pronosticos con el mejor modelo
###############

# Pronosticos muestrales
muestrales_rt_Y<-fitted(m4)
#ver los últimos 250 pronosticos muestrales
pron_muestral_rt<-tail(muestrales_rt_Y, 250)


writeClipboard(as.character(pron_muestral_rt))

#Obtener el pronostico muestral de los precios
precio_inicial<-Y[1] #Es el primer precio en la serie original

#Pronostico muestral de los precios a partir de la acumulacion de los pronosticos de los retornos
pronostico_precios_Y<-precio_inicial*cumprod(exp(muestrales_rt_Y))
tail(pronostico_precios_Y, 10)

#pronosticos extramuestrales

# Numero de pronosticos a realizar con el mejor modelo ARIMA
h <- 100

# Realizar pronostico extramuestral de retornos
pronostico_extram_retornos <- predict(m4, n.ahead=100) #valor puntual 
pron_extram_rt<-pronostico_extram_retornos$pred

writeClipboard(as.character(pron_extram_rt))

pronostico_extram_retornos <- forecast(m4, h) #valor puntual y el intervalo
#pronostico_extram_retornos

# Visualizacion de los pronosticos
plot(pronostico_extram_retornos, main="Pronosticos AR(1,8,9,22) Extramuestrales")


#Pronosticos extramuestrales de precios
pronostico_retornos1 <- pronostico_extram_retornos$mean  #Me entrega el valor puntual

pronostico_retornos1 <- as.numeric(pronostico_retornos1)  # Convierte a vector numérico


# Pronóstico de precios extramuestrales
ultimo_precio <- tail(Y, n = 1)
ultimo_precio <- as.numeric(ultimo_precio)
cumsum_pron_ret <- cumsum(pronostico_retornos1)
exp_cumsum <- exp(cumsum_pron_ret)
exp_cumsum <- as.numeric(exp_cumsum)
precios_futuros <- ultimo_precio * exp_cumsum
precios_futuros


#----------------------------------------------------------------------------------------------------------
data <- data.frame(rt_Y)

nrow(data)

n_total <- nrow(data)
n_test  <- 10
n_train <- n_total - n_test

train_data <- data[1:n_train, ]
test_data  <- data[(n_train+1):n_total, ]


#--------------------------------------------------------------------------------------------------------------------------
#estimacion del modelo
m4 <- Arima(train_data, order=c(22,0,0), fixed=c(NA, rep(0,6),NA,NA, rep(0,12),NA,NA),include.mean = TRUE,
            method = "CSS-ML",
            optim.method = "BFGS")
summary(m4)
coeftest(m4)

#----------------------------------------------------------------------------------------------------------------

#Pronóstico Dinamico

#PRONÓSTCOS EXTRAMUESTRALES CON ARIMA de retornos
forecast_values <- predict(m4, n.ahead=10)
retornos_pronosticados <- forecast_values$pred  # Valores predichos
retornos_pronosticados


#PRONOSTICO DE LOS ÚLTIMOS 10 PRECIOS. 
precios_reales <- tail(Y, 10)  # Últimos 10 precios reales
precio_anterior <- Y[n_train+1]  # Último precio conocido antes de pronóstico

# Reconstrucción de precios pronosticados a partir de los retornos
precios_pronosticados <- numeric(10) #Se crea el vector
precios_pronosticados[1] <- precio_anterior * exp(retornos_pronosticados[1])

for (i in 2:10) {
  precios_pronosticados[i] <- precios_pronosticados[i-1] * exp(retornos_pronosticados[i])
}

#Ver los precios_pronosticados
print(precios_pronosticados)

#-----------------------------------------------------------------------------------------------

#GRAFICAR PRECIOS REALES VS. PRONOSTICADOS

#Grafico
df_plot <- data.frame(
  Dia = 1:length(precios_reales),
  Precio_Real = as.numeric(precios_reales),
  Precio_Pronosticado = as.numeric(precios_pronosticados)
)

#Mejorar gráfico

ggplot(df_plot, aes(x = Dia)) +
  geom_line(aes(y = Precio_Real, color = "Real"), size = 1.2) +
  geom_point(aes(y = Precio_Real, color = "Real"), size = 2) +
  geom_line(aes(y = Precio_Pronosticado, color = "Pronosticado"),
            linetype = "dashed", size = 1.2) +
  geom_point(aes(y = Precio_Pronosticado, color = "Pronosticado"),
             size = 2, shape = 17) +
  theme_minimal() +
  labs(title = "📊 Precios Reales vs Pronosticados",
       subtitle = "Comparación últimos 10 días",
       x = "Día",
       y = "Precio de Y")

#----------------------------------------------------------------------------------------------------------------
#-----------------------------------------------------------------------------------------------------------------------

#Calcular el Mape de los 10 pronósticos
mape <- mean(abs((precios_reales - precios_pronosticados) / precios_reales)) * 100
print(paste("MAPE:", round(mape, 2), "%"))

# Calcular MAPE diario
mape_diario <- abs((precios_reales - precios_pronosticados) / precios_reales) * 100

# Calcular MAPE acumulado
mape_acumulado <- cumsum(mape_diario) / seq_along(mape_diario)
mape_acumulado

#----------------------------------------------------------------------------------------------
#Ahora obtener los pronosticos de los precios teniendo en cuenta el precio real del dia anterior

# Pronosticar los siguientes días usando SIEMPRE el precio real del día anterior

# Inicializar vector de precios pronosticados
precios_pronosticados_real <- numeric(10) 

# Primer precio pronosticado usando el precio real del día anterior
precios_pronosticados_real[1] <- precios_reales[1] * exp(retornos_pronosticados[1])
for (i in 2:10) {
  precios_pronosticados_real[i] <- precios_reales[i-1] * exp(retornos_pronosticados[i])
}

# Ver resultados
print(precios_pronosticados_real)

writeClipboard(as.character(precios_pronosticados_real))

#----------------------------------------------------------------------------------------------------------------------
#----------------------------------------------------------------------------------------------------------------------

#GRAFICAR PRECIOS REALES VS. PRONOSTICADOS estatico
df_plot <- data.frame(
  Dia = 1:length(precios_reales),
  Precio_Real = as.numeric(precios_reales),
  Precio_Pronosticado = as.numeric(precios_pronosticados_real)
)

# Gráfico mejorado

ggplot(df_plot, aes(x = Dia)) +
  geom_line(aes(y = Precio_Real, color = "Real"), size = 1.2) +
  geom_point(aes(y = Precio_Real, color = "Real"), size = 2) +
  geom_line(aes(y = Precio_Pronosticado, color = "Pronosticado"),
            linetype = "dashed", size = 1.2) +
  geom_point(aes(y = Precio_Pronosticado, color = "Pronosticado"),
             size = 2, shape = 17) +
  theme_minimal() +
  labs(title = "📊 Precios Reales vs Pronosticados",
       subtitle = "Comparación últimos 10 días",
       x = "Día",
       y = "Precio de Y")


#----------------------------------------------------------------------------------------------------

#Calcular el Mape de los 10 pronósticos a partir del precio real
mape <- mean(abs((precios_reales - precios_pronosticados_real) / precios_reales)) * 100
print(paste("MAPE:", round(mape, 2), "%"))

# Calcular MAPE diario
mape_diario <- abs((precios_reales - precios_pronosticados_real) / precios_reales) * 100

# Calcular MAPE acumulado
mape_acumulado <- cumsum(mape_diario) / seq_along(mape_diario)
mape_acumulado

#----------------------------------------------------------------------------------------------------------------

#RETORNOS CORREGIDOS POR LA MEDIA (RESIDUOS DEL ARIMA)
r <- as.numeric(residuals_m4)
n <- length(r)
alpha <- 0.05   # nivel de riesgo

#--------------------------------------------------------------------------------------------------------------------------

#OPTIMIZACIÓN DE LAMBDA EWMA (MINIMIZANDO RMSE)

window <- 21

ewma_rmse <- function(lambda, r, window){
#-------------------------
# VARIANZA EWMA
  sigma2_ewma <- rep(NA, n)
  sigma2_ewma[1] <- var(r, na.rm = TRUE)
  
  for(i in 2:n){
    sigma2_ewma[i] <- lambda * sigma2_ewma[i-1] + (1 - lambda) * r[i-1]^2
  }
  
  #-------------------------
  # VARIANZA CON VENTANA MOVIL
  sigma2_ma <- rep(NA, n)
  
  for(i in window:n){
    sigma2_ma[i] <- var(r[(i-window+1):i])
  }
  
  #-------------------------
  # ERROR ENTRE VARIANZAS
  error <- sigma2_ewma - sigma2_ma
  
  # RMSE
  rmse <- sqrt(mean(error[(window+1):n]^2, na.rm = TRUE))
  
  return(rmse)
}

#----------------------------------------------------------------------------------
# OPTIMIZACIÓN DE LAMBDA

opt <- optimize(function(lambda) ewma_rmse(lambda, r, window),
                interval = c(0.90, 0.999))

lambda_opt <- opt$minimum

lambda_opt

#-----------------------------------------------------------------------------------------------------------------

#VOLATILIDAD EWMA CON LAMBDA ÓPTIMO
sigma2 <- rep(NA,n)

sigma2[1] <- var(r)

for(i in 2:n){
  
  sigma2[i] <- lambda_opt*sigma2[i-1] +
    (1-lambda_opt)*r[i-1]^2
}

sigma <- sqrt(sigma2)

#--------------------------------------------------------------------------------------------------------------------

#VOLATILIDAD PARA T+1 Y RETORNO ESPERADO T+1 (ARIMA)

sigma_T1 <- sqrt(lambda_opt*sigma2[n] +
                   (1-lambda_opt)*r[n]^2)
sigma_T1

mu_T1 <- pron_extram_rt[1]
mu_T1


#------------------------------------------------------------------------------------------------------------------------

#VaR – SIMULACIÓN HISTÓRICA

VaR_hist <- quantile(r, alpha)

CVaR_hist <- mean(r[r <= VaR_hist])

VaR_hist
CVaR_hist

#----------------------------------------------------------------------------------------------------------------------------------------

#VaR – SIMULACIÓN MONTE CARLO (NORMAL)
set.seed(123)

Nsim <- 20000

sim_norm <- rnorm(Nsim, mu_T1, sigma_T1)

VaR_MC_norm <- quantile(sim_norm, alpha)

CVaR_MC_norm <- mean(sim_norm[sim_norm <= VaR_MC_norm])

VaR_MC_norm
CVaR_MC_norm

#----------------------------------------------------------------------------------------------------------------------

#VaR – MONTE CARLO COLAS PESADAS (t-Student)

# residuos del ARIMA
r <- as.numeric(residuals_m4)

library(moments)

kurtosis(r)
skewness(r)
qqnorm(r)
qqline(r, col="red")

# ajustar t-student
library(MASS)

fit_t <- fitdistr(r, "t")

df_est <- fit_t$estimate["df"]
df_est

library(car)

qqPlot(r, distribution="t", df=df_est)

Nsim <- 20000

sim_t <- mu_T1 + sigma_T1 * rt(Nsim, df_est)

VaR_MC_t <- quantile(sim_t, alpha)

CVaR_MC_t <- mean(sim_t[sim_t <= VaR_MC_t])

VaR_MC_t
CVaR_MC_t


#------------------------------------------------------------------------------------------------------------------------

#VaR – DISTRIBUCIÓN NORMAL
z <- qnorm(alpha)

VaR_normal <- mu_T1 + z*sigma_T1

CVaR_normal <- mu_T1 -
  sigma_T1*(dnorm(z)/alpha)

VaR_normal
CVaR_normal

#--------------------------------------------------------------------------------------------------------------------------------------

#VaR – t-STUDENT NO ESTANDARIZADA
qt_val <- qt(alpha,df_est)

VaR_t <- mu_T1 + qt_val*sigma_T1

sim_t2 <- mu_T1 + sigma_T1*rt(Nsim,df_est)

CVaR_t <- mean(sim_t2[sim_t2 <= VaR_t])

VaR_t
CVaR_t

#--------------------------------------------------------------------------------------------------------------------------------------------------------

#SIMULACIÓN HISTÓRICA FILTRADA (FHS)
z_hist <- r/sigma

sim_fhs <- mu_T1 + sigma_T1*z_hist

VaR_fhs <- quantile(sim_fhs,alpha)

CVaR_fhs <- mean(sim_fhs[sim_fhs <= VaR_fhs])

VaR_fhs
CVaR_fhs

#----------------------------------------------------------------------------------------------------------------------------------

#VaR POR MACHINE LEARNING (Random Forest)
library(randomForest)

df_ml <- data.frame(
  r = r[-1],
  vol = sigma[-length(sigma)]
)

rf_model <- randomForest(r~vol,data=df_ml)

df_T1 <- data.frame(vol=sigma_T1)

mu_ml <- predict(rf_model,df_T1)

sim_ml <- rnorm(Nsim,mu_ml,sigma_T1)

VaR_ml <- quantile(sim_ml,alpha)

CVaR_ml <- mean(sim_ml[sim_ml <= VaR_ml])

VaR_ml
CVaR_ml 

#-----------------------------------------------------------------------------------------------------------------------------------

#TABLA FINAL DE VaR

Resultados_Riesgo <- data.frame(
  
  Metodo=c("Historico",
           "MonteCarlo_Normal",
           "MonteCarlo_t",
           "Normal",
           "t_student",
           "Filtered_Historical",
           "Machine_Learning"),
  
  VaR=c(VaR_hist,
        VaR_MC_norm,
        VaR_MC_t,
        VaR_normal,
        VaR_t,
        VaR_fhs,
        VaR_ml),
  
  CVaR=c(CVaR_hist,
         CVaR_MC_norm,
         CVaR_MC_t,
         CVaR_normal,
         CVaR_t,
         CVaR_fhs,
         CVaR_ml)
)

Resultados_Riesgo
#-----------------------------------------------------------------------------------------------------------------------------------------------


writeClipboard(as.character(residuals_m1))


# Comparar T con normal

r <- as.numeric(residuals_m4)

# parámetros normal
mu <- mean(r)
sigma <- sd(r)

# ajustar t-student
fit_t <- fitdistr(r, "t",
                  start=list(m=mean(r),
                             s=sd(r),
                             df=6))

df_est <- fit_t$estimate["df"]

hist(r,
     probability = TRUE,
     breaks = 40,
     col = "lightgray",
     main = "Comparación de distribuciones",
     xlab = "Residuos ARIMA")

# normal
curve(dnorm(x, mu, sigma),
      col = "red",
      lwd = 2,
      add = TRUE)

# t-student
curve(dt((x-mu)/sigma, df_est)/sigma,
      col = "blue",
      lwd = 2,
      add = TRUE)

legend("topright",
       legend=c("Normal","t-Student"),
       col=c("red","blue"),
       lwd=2)
